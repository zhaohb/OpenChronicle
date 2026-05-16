"""LLM passes that turn deterministic stats + sub_tasks into a recap.

Two passes — same shape as ``meeting_task_digest`` and ``handover_assistant``:

1. ``theme_cluster.md``  → groups sub_tasks into themes (no time math).
2. ``weekly_recap.md``   → narrates the window using the themes + the
   pre-computed time-distribution table + (optional) previous window's recap.

The LLM never re-derives durations: every minute / percent comes from
:mod:`stats` or from a verbatim ``Observed regularity:`` line emitted by
OpenChronicle's session reducer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from shared import LLMClient
from shared.memory_loader import Entry, MemoryFile

from .stats import ActivityStats, SubTask, format_table

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(slots=True)
class Theme:
    name: str
    description: str
    apps: list[str]
    approx_minutes: int
    evidence_ranges: list[str]


@dataclass(slots=True)
class NotableOneOff:
    range: str
    note: str


# Substrings of app names (from one-off ``range``, after last ", ") that indicate
# mail / calendar / meeting clients for recap ordering and Markdown grouping.
_MAIL_MEETING_APP_SUBSTRINGS: tuple[str, ...] = (
    "outlook",
    "thunderbird",
    "mail",
    "teams",
    "zoom",
    "webex",
    "skype",
    "google meet",
    "facetime",
    "calendar",
    "日历",
)

# If ``range`` + ``note`` contains these, treat as mail/meeting-related even when
# the app is a generic browser (e.g. Outlook on the web).
_MAIL_MEETING_TEXT_MARKERS: tuple[str, ...] = (
    "meeting",
    "calendar",
    "invite",
    "inbox",
    "outbox",
    "composer",
    "zoom ",
    " teams",
    "webex",
    "outlook",
    "gmail",
    "邮件",
    "会议",
    "会邀",
    "日历",
    "邮箱",
    "回信",
    "转发",
    "邀请",
    "日程",
    "例会",
    "周会",
    "视频会议",
)

_PLACEHOLDER_ONE_OFF_MARKERS: tuple[str, ...] = (
    "session sess_",
    "#session",
    "(unspecified)",
)

_LOW_SIGNAL_ACTIVITY_MARKERS: tuple[str, ...] = (
    "viewed ",
    "browsed ",
    "opened ",
    "clicked ",
    "navigated ",
    "interacted with",
    "file explorer",
    "explorer section",
    "file tree",
    "typed \"\"",
    "empty string",
)

_HIGH_SIGNAL_ONE_OFF_MARKERS: tuple[str, ...] = (
    "deadline",
    "due",
    "follow up",
    "action item",
    "commitment",
    "decision",
    "decided",
    "blocked",
    "error",
    "exception",
    "failed",
    "failure",
    "incident",
    "bug",
    "draft",
    "reply",
    "send",
    "confirm",
    "submit",
    "review",
    "todo",
    "截止",
    "到期",
    "待办",
    "跟进",
    "确认",
    "提交",
    "回复",
    "草稿",
    "决定",
    "承诺",
    "阻塞",
    "错误",
    "异常",
    "失败",
)


def _app_suffix_from_one_off_range(range_str: str) -> str:
    s = range_str.strip()
    if ", " in s:
        return s.rsplit(", ", 1)[-1].strip().lower()
    return ""


def _one_off_duration_minutes(range_str: str) -> int:
    """Best-effort duration parser for `YYYY-MM-DD HH:MM-HH:MM, App` ranges."""
    m = re.search(
        r"(?P<start>\d{1,2}:\d{2})\s*[-\u2013]\s*(?P<end>\d{1,2}:\d{2})",
        range_str,
    )
    if not m:
        return 0
    try:
        start = datetime.strptime(m.group("start"), "%H:%M")
        end = datetime.strptime(m.group("end"), "%H:%M")
    except ValueError:
        return 0
    if end < start:
        end += timedelta(days=1)
    return int(round((end - start).total_seconds() / 60.0))


def is_mail_meeting_notable_one_off(o: NotableOneOff) -> bool:
    """Heuristic: used to order one-offs and group §五 in Markdown output."""
    app = _app_suffix_from_one_off_range(o.range)
    if any(frag in app for frag in _MAIL_MEETING_APP_SUBSTRINGS):
        return True
    blob = f"{o.range} {o.note}".lower()
    return any(m in blob for m in _MAIL_MEETING_TEXT_MARKERS)


def is_low_value_notable_one_off(o: NotableOneOff) -> bool:
    """Drop one-offs that lack durable value for a recap reader.

    This intentionally uses a general signal model rather than memorizing bad
    examples. Keep short one-offs only when they carry communication, deadline,
    decision, incident, unfinished-draft, or other recoverable-work signal.
    """
    blob = f"{o.range} {o.note}".lower()
    if is_mail_meeting_notable_one_off(o):
        return False
    if any(marker in blob for marker in _PLACEHOLDER_ONE_OFF_MARKERS):
        return True
    app = _app_suffix_from_one_off_range(o.range)
    if not app or app == "(unspecified)":
        return True
    if any(marker in blob for marker in _HIGH_SIGNAL_ONE_OFF_MARKERS):
        return False
    duration = _one_off_duration_minutes(o.range)
    if duration >= 30 and not any(
        marker in blob for marker in _LOW_SIGNAL_ACTIVITY_MARKERS
    ):
        return False
    if any(marker in blob for marker in _LOW_SIGNAL_ACTIVITY_MARKERS):
        return True
    if duration < 10:
        return True
    return False


def prioritize_mail_meeting_one_offs(one_offs: list[NotableOneOff]) -> list[NotableOneOff]:
    """Stable sort: mail/calendar/meeting-related entries first."""
    if len(one_offs) < 2:
        return one_offs
    keyed = [(i, o) for i, o in enumerate(one_offs)]
    keyed.sort(
        key=lambda t: (0 if is_mail_meeting_notable_one_off(t[1]) else 1, t[0])
    )
    return [o for _, o in keyed]


@dataclass(slots=True)
class ChangeItem:
    kind: str
    note: str


@dataclass(slots=True)
class OpenThread:
    """One unfinished strand at end of window — structured for recap §九."""

    topic: str = ""
    last_status: str = ""
    last_seen: str = ""
    last_snapshot: str = ""
    why_unfinished: str = ""
    grounded_in: str = ""

    def is_legacy_flat_sentence(self) -> bool:
        """Older weekly_recap output: a single prose line, no structured fields."""
        snap = self.last_snapshot.strip()
        if not snap or "\n" in snap:
            return False
        return not any(
            (
                self.topic.strip(),
                self.last_status.strip(),
                self.last_seen.strip(),
                self.why_unfinished.strip(),
                self.grounded_in.strip(),
            )
        )


@dataclass(slots=True)
class Recap:
    """The full long-term-memory artifact for one window."""

    since: date
    until: date
    window_label: str
    generated_at: datetime
    stats: ActivityStats
    headline: str = ""
    summary: str = ""
    time_breakdown_note: str = ""
    themes: list[Theme] = field(default_factory=list)
    regularities: list[str] = field(default_factory=list)
    change_vs_previous: list[ChangeItem] = field(default_factory=list)
    open_threads: list[OpenThread] = field(default_factory=list)
    coverage_note: str = ""
    coverage_minutes: int = 0
    notable_one_offs: list[NotableOneOff] = field(default_factory=list)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Prompt-input formatters
# ---------------------------------------------------------------------------

def _format_subtasks(sub_tasks: Iterable[SubTask], max_lines: int = 600) -> str:
    """Compact, deterministic listing of every sub_task in the window.

    Cap to ``max_lines`` to stay inside small models' context. We trim from
    the *middle* (keep the most recent + the oldest), which preserves the
    bookends a recap needs.
    """
    lines = [
        f"[{s.day.isoformat()} {s.start.strftime('%H:%M')}-{s.end.strftime('%H:%M')}, {s.app}] {s.text}"
        for s in sub_tasks
    ]
    if len(lines) <= max_lines:
        return "\n".join(lines) if lines else "(no sub_tasks parsed)"
    head = lines[: max_lines // 2]
    tail = lines[-max_lines // 2 :]
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... [{omitted} sub_tasks omitted to fit context] ..."] + tail)


def _extract_observed_regularities(entries: Iterable[Entry]) -> list[str]:
    """Pull every `Observed regularity:` sentence the reducer left for us.

    These are the gold input for the recap's ``regularities`` field — they
    were grounded by the upstream pipeline against actual timeline blocks.
    """
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        body = entry.body or ""
        for line in body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            idx = stripped.find("Observed regularity:")
            if idx < 0:
                continue
            sentence = stripped[idx:].rstrip()
            if sentence in seen:
                continue
            seen.add(sentence)
            out.append(sentence)
    return out


def _format_durable_files(files: Iterable[MemoryFile]) -> str:
    rows: list[str] = []
    for f in files:
        desc = (f.description or "").replace("\n", " ").strip()
        rows.append(f"- {f.path}: {desc[:240]}")
    return "\n".join(rows) if rows else "(none)"


# ---------------------------------------------------------------------------
# Coercion helpers — keep the LLM honest about JSON shape
# ---------------------------------------------------------------------------

def _coerce_themes(raw: Any) -> list[Theme]:
    out: list[Theme] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        try:
            mins = int(item.get("approx_minutes", 0) or 0)
        except (TypeError, ValueError):
            mins = 0
        evidence = [str(x).strip() for x in (item.get("evidence_ranges") or []) if x]
        if not evidence:
            # A theme with no cited range fails the prompt's own anti-hallucination
            # rule — drop it rather than render a phantom strand of work.
            continue
        out.append(
            Theme(
                name=name,
                description=str(item.get("description", "")).strip(),
                apps=[str(x).strip() for x in (item.get("apps") or []) if x],
                approx_minutes=max(0, mins),
                evidence_ranges=evidence,
            )
        )
    return out


def _coerce_one_offs(raw: Any) -> list[NotableOneOff]:
    out: list[NotableOneOff] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        rng = str(item.get("range", "")).strip()
        note = str(item.get("note", "")).strip()
        if not (rng and note):
            continue
        one_off = NotableOneOff(range=rng, note=note)
        if is_low_value_notable_one_off(one_off):
            continue
        out.append(one_off)
    return out


def _coerce_open_threads(raw: Any) -> list[OpenThread]:
    """Parse Pass-2 ``open_threads`` — structured objects or legacy one-line strings."""
    out: list[OpenThread] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(OpenThread(last_snapshot=s))
            continue
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic", "") or "").strip()
        last_status = str(
            item.get("last_status", "") or item.get("last_state", "") or ""
        ).strip()
        last_seen = str(
            item.get("last_seen", "") or item.get("last_seen_range", "") or ""
        ).strip()
        last_snapshot = str(
            item.get("last_snapshot", "")
            or item.get("verbatim", "")
            or item.get("last_text", "")
            or ""
        ).strip()
        why_unfinished = str(
            item.get("why_unfinished", "") or item.get("reason", "") or ""
        ).strip()
        grounded_in = str(
            item.get("grounded_in", "") or item.get("source", "") or ""
        ).strip()
        if not any(
            (topic, last_status, last_seen, last_snapshot, why_unfinished, grounded_in)
        ):
            continue
        out.append(
            OpenThread(
                topic=topic,
                last_status=last_status,
                last_seen=last_seen,
                last_snapshot=last_snapshot,
                why_unfinished=why_unfinished,
                grounded_in=grounded_in,
            )
        )
    return out[:5]


def _coerce_changes(raw: Any) -> list[ChangeItem]:
    out: list[ChangeItem] = []
    if not isinstance(raw, list):
        return out
    allowed = {"new_theme", "dropped_theme", "app_shift", "tempo_shift"}
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "")).strip()
        note = str(item.get("note", "")).strip()
        if not note:
            continue
        if kind not in allowed:
            kind = "tempo_shift"
        out.append(ChangeItem(kind=kind, note=note))
    return out


# ---------------------------------------------------------------------------
# LLM passes
# ---------------------------------------------------------------------------

def _cluster_themes(
    llm: LLMClient,
    stats: ActivityStats,
    sub_tasks: list[SubTask],
    durable: list[MemoryFile],
) -> tuple[list[Theme], list[NotableOneOff], int]:
    """Pass 1 — cluster sub_tasks into themes."""
    if not sub_tasks:
        return [], [], 0

    sub_tasks_text = _format_subtasks(sub_tasks)
    stats_text = format_table(stats)
    durable_text = _format_durable_files(durable)
    system = _load_prompt("theme_cluster.md").format(
        since=stats.since.isoformat(),
        until=stats.until.isoformat(),
        sub_task_count=stats.sub_task_count,
        total_minutes=stats.total_minutes,
        sub_tasks_text=sub_tasks_text,
        stats_text=stats_text,
        durable_text=durable_text,
    )
    user_payload = "Return the JSON object per the schema. Output only JSON, no markdown fences."
    response = llm.chat(system=system, user=user_payload, json_mode=True)
    if not isinstance(response, dict):
        logger.warning("Theme-cluster pass returned non-dict; treating as empty.")
        return [], [], 0
    themes = _coerce_themes(response.get("themes"))
    one_offs = prioritize_mail_meeting_one_offs(
        _coerce_one_offs(response.get("notable_one_offs"))
    )
    try:
        coverage = int(response.get("coverage_minutes", 0) or 0)
    except (TypeError, ValueError):
        coverage = 0
    if coverage <= 0:
        # Recompute from themes if the model omitted the field.
        coverage = sum(t.approx_minutes for t in themes)
    return themes, one_offs, coverage


def _format_themes_for_recap(themes: Iterable[Theme]) -> str:
    rows: list[str] = []
    for t in themes:
        rows.append(f"### {t.name} ({t.approx_minutes} min)")
        if t.apps:
            rows.append(f"apps: {', '.join(t.apps)}")
        if t.description:
            rows.append(t.description)
        if t.evidence_ranges:
            rows.append("evidence:")
            for r in t.evidence_ranges:
                rows.append(f"  - {r}")
        rows.append("")
    return "\n".join(rows) if rows else "(no themes)"


def _format_one_offs(one_offs: Iterable[NotableOneOff]) -> str:
    rows = [f"- [{o.range}] {o.note}" for o in one_offs]
    return "\n".join(rows) if rows else "(none)"


def _format_previous_recap(prev: Recap | None) -> str:
    if prev is None:
        return "(no previous-window recap provided)"
    rows = [
        f"window: {prev.since.isoformat()} → {prev.until.isoformat()}",
        f"headline: {prev.headline}",
        "themes:",
    ]
    for t in prev.themes:
        rows.append(f"  - {t.name}: {t.approx_minutes} min ({', '.join(t.apps)})")
    rows.append(f"top_apps_table:")
    for app, mins in prev.stats.top_apps(8):
        rows.append(f"  - {app}: {mins} min")
    return "\n".join(rows)


def _synthesize_recap_pass(
    llm: LLMClient,
    stats: ActivityStats,
    themes: list[Theme],
    one_offs: list[NotableOneOff],
    regularities: list[str],
    durable: list[MemoryFile],
    previous: Recap | None,
    window_label: str,
) -> dict[str, Any]:
    """Pass 2 — narrate the window."""
    stats_text = format_table(stats)
    themes_text = _format_themes_for_recap(themes)
    notable_text = _format_one_offs(one_offs)
    regularities_text = (
        "\n".join(f"- {r}" for r in regularities) if regularities else "(none)"
    )
    durable_text = _format_durable_files(durable)
    previous_text = _format_previous_recap(previous)
    system = _load_prompt("weekly_recap.md").format(
        since=stats.since.isoformat(),
        until=stats.until.isoformat(),
        window_label=window_label,
        stats_text=stats_text,
        themes_text=themes_text,
        notable_text=notable_text,
        regularities_text=regularities_text,
        durable_text=durable_text,
        previous_text=previous_text,
    )
    user_payload = "Return the JSON object per the schema. Output only JSON, no markdown fences."
    response = llm.chat(system=system, user=user_payload, json_mode=True)
    if not isinstance(response, dict):
        logger.warning("Recap pass returned non-dict; rendering minimal fallback.")
        return {}
    return response


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def synthesize_recap(
    llm: LLMClient,
    stats: ActivityStats,
    sub_tasks: list[SubTask],
    event_entries: list[Entry],
    durable: list[MemoryFile],
    previous: Recap | None,
    window_label: str,
) -> Recap:
    """Run both LLM passes and assemble a :class:`Recap`."""
    recap = Recap(
        since=stats.since,
        until=stats.until,
        window_label=window_label,
        generated_at=datetime.now(),
        stats=stats,
    )

    if stats.total_minutes <= 0 or not sub_tasks:
        recap.headline = "本窗口内未捕获到可统计的桌面活动。"
        recap.summary = (
            "OpenChronicle 在该时间范围内没有写入 event-daily 中的 sub_task 行 — "
            "可能是 daemon 未运行、还没积累足够数据，或 reducer 阶段未产出。"
        )
        recap.coverage_note = "0% 覆盖：该窗口没有可识别的 sub_task。"
        return recap

    themes, one_offs, coverage = _cluster_themes(llm, stats, sub_tasks, durable)
    recap.themes = themes
    recap.notable_one_offs = one_offs
    recap.coverage_minutes = coverage

    regularities = _extract_observed_regularities(event_entries)

    response = _synthesize_recap_pass(
        llm,
        stats,
        themes,
        one_offs,
        regularities,
        durable,
        previous,
        window_label,
    )

    recap.headline = str(response.get("headline", "")).strip()
    recap.summary = str(response.get("summary", "")).strip()
    recap.time_breakdown_note = str(response.get("time_breakdown_note", "")).strip()
    recap.coverage_note = str(response.get("coverage_note", "")).strip()
    recap.regularities = [
        str(x).strip() for x in (response.get("regularities") or []) if str(x).strip()
    ]
    recap.change_vs_previous = _coerce_changes(response.get("change_vs_previous"))
    recap.open_threads = _coerce_open_threads(response.get("open_threads"))

    # If the recap pass forgot the regularities, fall back to the upstream
    # pipeline's own grounded sentences — the prompt explicitly says these
    # are pre-grounded, so it's safe to surface them as-is.
    if not recap.regularities and regularities:
        recap.regularities = regularities[:5]

    return recap


__all__ = [
    "Theme",
    "NotableOneOff",
    "ChangeItem",
    "OpenThread",
    "Recap",
    "is_mail_meeting_notable_one_off",
    "prioritize_mail_meeting_one_offs",
    "synthesize_recap",
]
