"""Deterministic time-distribution statistics over event-daily sub_tasks.

We do **not** ask the LLM to count minutes — small models will hallucinate
percentages. Instead, we parse every sub_task line of the form

    [HH:MM-HH:MM, <app name>] <action>; <verbatim>; involving <...>

into a :class:`SubTask` record, then compute totals over apps, weekdays, and
hour-of-day buckets. The LLM (in :mod:`synthesizer`) is only fed the resulting
ranked tables plus the raw sub_task text — it never re-derives time math.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable

from shared.memory_loader import Entry


# Matches the canonical sub_task prefix produced by session_reduce.md:
#   "- [10:00-10:02, OneDrive] ..." or "- [10:00–10:02, OneDrive] ..."
# The hyphen between times can be ASCII '-' or unicode en-dash '–'.
_SUBTASK_RE = re.compile(
    r"""
    ^\s*-\s*\[                       # leading "- ["
    (?P<start>\d{1,2}:\d{2})         # start time
    \s*[-\u2013]\s*                  # hyphen or en-dash
    (?P<end>\d{1,2}:\d{2})           # end time
    ,\s*
    (?P<app>[^\]]+?)                 # app name, non-greedy until ]
    \]\s*
    (?P<rest>.*)$                    # remainder of the line
    """,
    re.VERBOSE,
)

# Match a "Session sess_xxx" header line with embedded HH:MM-HH:MM range; we
# use it as a fallback when an entry has no sub_task lines (older formats).
_SESSION_HEADER_RE = re.compile(
    r"\*\*Session\s+(?P<sid>[^*]+?)\*\*\s*\((?P<start>\d{1,2}:\d{2})\s*[-\u2013]\s*(?P<end>\d{1,2}:\d{2})\)"
)

_RAW_BREADCRUMB_RE = re.compile(r"\s+—\s+raw:.*$")


def _looks_like_inbox_list(text: str) -> bool:
    """True for mailbox list views, not an opened concrete email thread."""
    lower = text.lower()
    return (
        "inbox list" in lower
        or "viewed inbox" in lower
        or "viewed gmail inbox" in lower
        or lower.startswith("收件箱")
    )


def _sanitize_inbox_list_text(text: str) -> str:
    """Avoid cross-attributing subjects/senders from mailbox list snapshots.

    Inbox list captures often contain many unrelated visible rows. Small models can
    incorrectly turn that into one statement like "noted a message from Cursor
    about X" while also mixing another subject in `Involving`. For recap purposes,
    a list view should stay a list view; concrete email details belong only to
    separate opened-email sub_tasks.
    """
    if not _looks_like_inbox_list(text):
        return text

    raw_match = _RAW_BREADCRUMB_RE.search(text)
    raw_suffix = raw_match.group(0) if raw_match else ""
    core = text[: raw_match.start()] if raw_match else text
    first_clause = core.split(";", 1)[0].strip()
    first_clause = first_clause.split(". Involving:", 1)[0].strip()

    if first_clause.lower().startswith("收件箱"):
        cleaned = "Inbox list: browsed inbox list. Involving: inbox list."
    else:
        cleaned = first_clause.rstrip(".") + ". Involving: inbox list."
    return cleaned + raw_suffix


@dataclass(slots=True)
class SubTask:
    """One time-ranged sub_task observation from event-daily."""

    day: date
    start: datetime
    end: datetime
    app: str
    text: str
    entry_id: str

    @property
    def duration_minutes(self) -> int:
        delta = (self.end - self.start).total_seconds() / 60.0
        return int(round(max(delta, 0)))

    @property
    def weekday(self) -> int:
        # 0 = Monday, 6 = Sunday — matches datetime.weekday().
        return self.day.weekday()

    @property
    def hour_bucket(self) -> str:
        h = self.start.hour
        if h < 6:
            return "early-morning"   # 00:00 – 05:59
        if h < 12:
            return "morning"         # 06:00 – 11:59
        if h < 14:
            return "midday"          # 12:00 – 13:59
        if h < 18:
            return "afternoon"       # 14:00 – 17:59
        if h < 22:
            return "evening"         # 18:00 – 21:59
        return "late-night"          # 22:00 – 23:59


@dataclass(slots=True)
class ActivityStats:
    """Aggregated, deterministic time distribution for one window."""

    since: date
    until: date
    total_minutes: int = 0
    sub_task_count: int = 0
    by_app: dict[str, int] = field(default_factory=dict)
    by_weekday: dict[str, int] = field(default_factory=dict)
    by_hour_bucket: dict[str, int] = field(default_factory=dict)
    by_day: dict[str, int] = field(default_factory=dict)
    sub_tasks: list[SubTask] = field(default_factory=list)

    def top_apps(self, n: int = 8) -> list[tuple[str, int]]:
        return sorted(self.by_app.items(), key=lambda kv: kv[1], reverse=True)[:n]

    def percent(self, minutes: int) -> float:
        if self.total_minutes <= 0:
            return 0.0
        return round(minutes * 100.0 / self.total_minutes, 1)


_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_subtask_line(line: str, day: date, entry_id: str) -> SubTask | None:
    m = _SUBTASK_RE.match(line)
    if not m:
        return None
    try:
        start_t = time.fromisoformat(m.group("start"))
        end_t = time.fromisoformat(m.group("end"))
    except ValueError:
        return None
    start_dt = datetime.combine(day, start_t)
    end_dt = datetime.combine(day, end_t)
    # Sub-task crossing midnight: roll end to next day.
    if end_dt < start_dt:
        end_dt = end_dt + timedelta(days=1)
    app = m.group("app").strip()
    rest = _sanitize_inbox_list_text(m.group("rest").strip())
    return SubTask(
        day=day,
        start=start_dt,
        end=end_dt,
        app=app,
        text=rest,
        entry_id=entry_id,
    )


def _day_from_entry(entry: Entry) -> date | None:
    """Best-effort: pick the calendar day an entry belongs to.

    Prefers the file path (`event-YYYY-MM-DD.md`) which is canonical;
    falls back to the entry's timestamp.
    """
    m = re.search(r"event-(\d{4}-\d{2}-\d{2})\.md", entry.path or "")
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    if entry.timestamp and entry.timestamp != datetime.min:
        return entry.timestamp.date()
    return None


def parse_event_entries(entries: Iterable[Entry]) -> list[SubTask]:
    """Pull every parseable sub_task line out of a stream of event-daily entries."""
    out: list[SubTask] = []
    for entry in entries:
        day = _day_from_entry(entry)
        if day is None:
            continue
        body = entry.body or ""
        found_any = False
        for raw_line in body.splitlines():
            sub = _parse_subtask_line(raw_line, day, entry.id)
            if sub is not None:
                out.append(sub)
                found_any = True
        # Fallback: an entry without sub_task lines but with a "Session
        # sess_xxx (HH:MM-HH:MM)" header still gives us *some* bracketed time.
        # We treat it as one synthetic record on app="(unknown)".
        if not found_any:
            mh = _SESSION_HEADER_RE.search(body)
            if mh:
                try:
                    start_t = time.fromisoformat(mh.group("start"))
                    end_t = time.fromisoformat(mh.group("end"))
                except ValueError:
                    continue
                start_dt = datetime.combine(day, start_t)
                end_dt = datetime.combine(day, end_t)
                if end_dt < start_dt:
                    end_dt = end_dt + timedelta(days=1)
                out.append(
                    SubTask(
                        day=day,
                        start=start_dt,
                        end=end_dt,
                        app="(unspecified)",
                        text=body.strip().splitlines()[0][:200] if body.strip() else "",
                        entry_id=entry.id,
                    )
                )
    out.sort(key=lambda s: s.start)
    return out


def compute_stats(
    sub_tasks: list[SubTask],
    since: date,
    until: date,
) -> ActivityStats:
    """Reduce a list of :class:`SubTask` into bucketed totals, in pure Python."""
    stats = ActivityStats(since=since, until=until, sub_tasks=list(sub_tasks))
    by_app: dict[str, int] = defaultdict(int)
    by_weekday: dict[str, int] = defaultdict(int)
    by_hour_bucket: dict[str, int] = defaultdict(int)
    by_day: dict[str, int] = defaultdict(int)
    total = 0
    for st in sub_tasks:
        # A sub_task on the boundary may land outside the requested window
        # (e.g. user asked for one ISO week but pulled a full file). Clip.
        if st.day < since or st.day > until:
            continue
        m = st.duration_minutes
        if m <= 0:
            continue
        total += m
        by_app[st.app] += m
        by_weekday[_WEEKDAY_NAMES[st.weekday]] += m
        by_hour_bucket[st.hour_bucket] += m
        by_day[st.day.isoformat()] += m
        stats.sub_task_count += 1
    stats.total_minutes = total
    stats.by_app = dict(by_app)
    stats.by_weekday = dict(by_weekday)
    stats.by_hour_bucket = dict(by_hour_bucket)
    stats.by_day = dict(by_day)
    return stats


def format_table(stats: ActivityStats, top_n: int = 10) -> str:
    """Render the deterministic stats as a compact Markdown block.

    This is what we feed to the LLM as ground-truth time math, so the LLM
    never has to compute percentages itself.
    """
    if stats.total_minutes <= 0:
        return "_no parseable activity in this window_"
    lines: list[str] = []
    lines.append(
        f"**Window**: {stats.since.isoformat()} → {stats.until.isoformat()} "
        f"({stats.total_minutes} min total across {stats.sub_task_count} sub_tasks)"
    )
    lines.append("")
    lines.append("**Top apps by time**")
    for app, mins in stats.top_apps(top_n):
        lines.append(f"- {app}: {mins} min ({stats.percent(mins)}%)")
    lines.append("")
    lines.append("**By weekday (min)**")
    for name in _WEEKDAY_NAMES:
        if name in stats.by_weekday:
            lines.append(f"- {name}: {stats.by_weekday[name]}")
    lines.append("")
    lines.append("**By time-of-day (min)**")
    for bucket in (
        "early-morning",
        "morning",
        "midday",
        "afternoon",
        "evening",
        "late-night",
    ):
        if bucket in stats.by_hour_bucket:
            lines.append(f"- {bucket}: {stats.by_hour_bucket[bucket]}")
    lines.append("")
    lines.append("**By day (min)**")
    for day_str in sorted(stats.by_day.keys()):
        lines.append(f"- {day_str}: {stats.by_day[day_str]}")
    return "\n".join(lines)


def _snippet_from_subtask_text(text: str) -> str:
    """Normalize sub_task text for timeline rows without truncating evidence."""
    s = (text or "").strip()
    if not s:
        return "(no description)"
    return re.sub(r"\s+", " ", s)


def _context_key_from_subtask_text(text: str) -> str:
    """Best-effort stable context key for deciding whether timeline rows merge.

    Many sub_tasks have the shape ``<context>: <action>; involving ...`` where
    ``context`` is a file name, page title, email subject, inbox label, or
    conversation name. Adjacent rows should only merge when that context is the
    same; sharing the same app alone is too broad and can hide unrelated emails
    or documents.
    """
    s = _RAW_BREADCRUMB_RE.sub("", _snippet_from_subtask_text(text)).strip()
    s = re.sub(r"\s+", " ", s)
    if not s:
        return ""

    # Prefer explicit context before the first colon. This covers pages, files,
    # email subjects, and many window-title based reducer outputs.
    prefix = s.split(":", 1)[0].strip() if ":" in s else ""
    if prefix and 3 <= len(prefix) <= 180:
        return prefix.lower()

    # Fall back to the first clause. This keeps generic repeated actions
    # mergeable while still preventing distinct quoted subjects from collapsing.
    first_clause = re.split(r";|\. Involving:|\binvolving\b", s, maxsplit=1, flags=re.I)[
        0
    ].strip()
    first_clause = re.sub(r"\d+", "#", first_clause)
    return first_clause[:180].lower()


@dataclass(slots=True)
class _TimelineSeg:
    start: datetime
    end: datetime
    display_app: str
    app_key: str
    context_key: str
    snippets: list[str]


def _merge_subtasks_for_timeline(
    sub_tasks: list[SubTask],
    *,
    merge_gap_minutes: float,
) -> list[_TimelineSeg]:
    """Merge adjacent same-app segments when the quiet gap ≤ merge_gap_minutes."""
    if not sub_tasks:
        return []
    out: list[_TimelineSeg] = []
    cur: _TimelineSeg | None = None
    for st in sub_tasks:
        app_key = st.app.strip().lower()
        context_key = _context_key_from_subtask_text(st.text)
        if cur is None:
            cur = _TimelineSeg(
                start=st.start,
                end=st.end,
                display_app=st.app.strip(),
                app_key=app_key,
                context_key=context_key,
                snippets=[_snippet_from_subtask_text(st.text)],
            )
            continue
        gap = (st.start - cur.end).total_seconds() / 60.0
        same_day = st.start.date() == cur.start.date()
        same_app = app_key == cur.app_key
        same_context = context_key == cur.context_key
        # Overlap or tiny negative clock skew — fold in.
        merge = same_app and same_day and same_context and (gap <= merge_gap_minutes or gap < 0)
        if merge:
            if st.end > cur.end:
                cur.end = st.end
            cur.snippets.append(_snippet_from_subtask_text(st.text))
        else:
            out.append(cur)
            cur = _TimelineSeg(
                start=st.start,
                end=st.end,
                display_app=st.app.strip(),
                app_key=app_key,
                context_key=context_key,
                snippets=[_snippet_from_subtask_text(st.text)],
            )
    if cur is not None:
        out.append(cur)
    return out


def build_compact_timeline_lines(
    stats: ActivityStats,
    *,
    max_segments: int = 44,
) -> list[str]:
    """Deterministic, adaptive-gap timeline for Markdown.

    Keep the original sub_task granularity when it already fits ``max_segments``.
    Only dense windows are compacted by progressively merging adjacent same-day /
    same-app / same-context bursts. This preserves evidence by default while
    still preventing pathological event files from producing huge recaps.
    """
    sts = [
        st
        for st in stats.sub_tasks
        if stats.since <= st.day <= stats.until and st.duration_minutes > 0
    ]
    sts.sort(key=lambda s: s.start)
    if not sts:
        return []
    # Hard cap — pathological event files should not blow up render time.
    sts = sts[:3000]

    merged = _merge_subtasks_for_timeline(sts, merge_gap_minutes=0.0)
    if len(merged) > max_segments:
        gap_schedule = (6, 12, 20, 35, 55, 90, 150, 240, 360, 720)
        for gap in gap_schedule:
            merged = _merge_subtasks_for_timeline(sts, merge_gap_minutes=float(gap))
            if len(merged) <= max_segments:
                break

    lines: list[str] = []
    prev_date: date | None = None
    for seg in merged:
        d = seg.start.date()
        if prev_date != d:
            if prev_date is not None:
                lines.append("")
            lines.append(f"### {d.isoformat()}")
            lines.append("")
            prev_date = d
        dur = int(round(max((seg.end - seg.start).total_seconds() / 60.0, 0)))
        if seg.end.date() != seg.start.date():
            span = f"{seg.start.strftime('%m-%d %H:%M')} → {seg.end.strftime('%m-%d %H:%M')}"
        else:
            span = f"{seg.start.strftime('%H:%M')}–{seg.end.strftime('%H:%M')}"
        summary = seg.snippets[0]
        if len(seg.snippets) > 1:
            summary = f"{summary} ({len(seg.snippets)} adjacent records merged)"
        lines.append(
            f"- **{span}** · `{seg.display_app}` · about **{dur}** min — {summary}"
        )
    return lines
