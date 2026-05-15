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
    rest = m.group("rest").strip()
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
