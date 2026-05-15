"""Core extraction logic for the meeting + task digest example.

The pipeline:

1. Pull every event-daily entry in the requested date range via :func:`iter_event_entries`.
2. Render the entries into a single text block grouped by day.
3. Ask the LLM (twice) to extract meetings and standalone tasks.
4. Return a :class:`DigestResult` that the CLI renders to Markdown.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from shared import LLMClient, OCMCPClient
from shared.memory_loader import Entry, iter_event_entries

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(slots=True)
class ActionItem:
    owner: str
    content: str
    deadline: str | None = None
    context: str = ""
    confidence: str = "medium"


@dataclass(slots=True)
class MeetingDigest:
    topic: str
    app: str
    time_range: str
    date: str
    attendees: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    related_links: list[str] = field(default_factory=list)
    source_session_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DigestResult:
    since: date
    until: date
    meetings: list[MeetingDigest] = field(default_factory=list)
    standalone_tasks: list[ActionItem] = field(default_factory=list)
    entry_count: int = 0


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _format_entries(entries: list[Entry]) -> str:
    """Render entries as a compact text block the LLM can reason over."""
    by_day: dict[str, list[Entry]] = {}
    for entry in entries:
        day = entry.timestamp.date().isoformat()
        by_day.setdefault(day, []).append(entry)

    lines: list[str] = []
    for day in sorted(by_day):
        lines.append(f"\n## Day {day}\n")
        for entry in by_day[day]:
            sid = entry.session_id or "?"
            ts = entry.timestamp.strftime("%H:%M")
            tag_str = " ".join(f"#{t}" for t in entry.tags if not t.startswith("sid:"))
            lines.append(f"- [{ts}] (session={sid}) {tag_str}")
            for body_line in entry.body.splitlines():
                if body_line.strip():
                    lines.append(f"    {body_line.rstrip()}")
    return "\n".join(lines)


def _coerce_action_items(raw: Any) -> list[ActionItem]:
    items: list[ActionItem] = []
    if not isinstance(raw, list):
        return items
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        items.append(
            ActionItem(
                owner=str(entry.get("owner", "me")),
                content=str(entry.get("content", "")).strip(),
                deadline=entry.get("deadline"),
                context=str(entry.get("context", "")).strip(),
                confidence=str(entry.get("confidence", "medium")),
            )
        )
    return [it for it in items if it.content]


def _coerce_meetings(raw: Any) -> list[MeetingDigest]:
    meetings: list[MeetingDigest] = []
    if not isinstance(raw, list):
        return meetings
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        meetings.append(
            MeetingDigest(
                topic=str(entry.get("topic", "Untitled")).strip(),
                app=str(entry.get("app", "unknown")),
                time_range=str(entry.get("time_range", "")),
                date=str(entry.get("date", "")),
                attendees=[str(x) for x in entry.get("attendees", []) if x],
                decisions=[str(x).strip() for x in entry.get("decisions", []) if x],
                action_items=_coerce_action_items(entry.get("action_items", [])),
                related_links=[str(x) for x in entry.get("related_links", []) if x],
                source_session_ids=[str(x) for x in entry.get("source_session_ids", []) if x],
            )
        )
    return meetings


async def extract_digest(
    mcp: OCMCPClient,
    llm: LLMClient,
    since: date,
    until: date,
) -> DigestResult:
    """Pull entries in [since, until] and run the two-pass extraction."""
    entries: list[Entry] = []
    async for entry in iter_event_entries(mcp, since, until):
        entries.append(entry)

    result = DigestResult(since=since, until=until, entry_count=len(entries))
    if not entries:
        logger.warning("No event-daily entries in [%s, %s]", since, until)
        return result

    rendered = _format_entries(entries)

    meeting_response = llm.chat(
        system=_load_prompt("meeting_extract.md"),
        user=f"Here are the event-daily entries:\n{rendered}\n\nReturn the JSON now.",
        json_mode=True,
    )
    if isinstance(meeting_response, dict):
        result.meetings = _coerce_meetings(meeting_response.get("meetings"))
        result.standalone_tasks = _coerce_action_items(
            meeting_response.get("standalone_tasks")
        )

    # Second pass: dedicated task extraction, in case meetings prompt missed loose tasks.
    task_response = llm.chat(
        system=_load_prompt("task_extract.md"),
        user=f"Here are the event-daily entries:\n{rendered}\n\nReturn the JSON now.",
        json_mode=True,
    )
    if isinstance(task_response, dict):
        extra = _coerce_action_items(task_response.get("tasks"))
        existing = {(t.owner.lower(), t.content.lower()) for t in result.standalone_tasks}
        for meeting in result.meetings:
            for it in meeting.action_items:
                existing.add((it.owner.lower(), it.content.lower()))
        for task in extra:
            key = (task.owner.lower(), task.content.lower())
            if key not in existing:
                result.standalone_tasks.append(task)
                existing.add(key)

    return result


def render_markdown(result: DigestResult) -> str:
    """Render the digest into a Markdown document."""
    lines: list[str] = []
    title_range = (
        result.since.isoformat()
        if result.since == result.until
        else f"{result.since.isoformat()} ~ {result.until.isoformat()}"
    )
    lines.append(f"# 会议与任务沉淀 · {title_range}")
    lines.append("")
    lines.append(
        f"_来源：OpenChronicle event-daily entries（共 {result.entry_count} 条），"
        f"生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}_"
    )
    lines.append("")

    if not result.meetings and not result.standalone_tasks:
        lines.append("> 没有从记忆中识别出会议或独立待办。")
        return "\n".join(lines)

    if result.meetings:
        lines.append("## 一、会议汇总")
        lines.append("")
        lines.append("| 日期 | 时间 | 主题 | 应用 | 参与人 |")
        lines.append("|---|---|---|---|---|")
        for m in result.meetings:
            attendees = ", ".join(m.attendees) or "—"
            lines.append(
                f"| {m.date or '—'} | {m.time_range or '—'} | {m.topic} | {m.app} | {attendees} |"
            )
        lines.append("")

        for m in result.meetings:
            lines.append(f"### 📌 {m.topic}")
            lines.append("")
            lines.append(f"- **时间**：{m.date} {m.time_range}")
            lines.append(f"- **应用**：{m.app}")
            if m.attendees:
                lines.append(f"- **参与人**：{', '.join(m.attendees)}")
            if m.related_links:
                links = ", ".join(f"[{u}]({u})" for u in m.related_links)
                lines.append(f"- **相关链接**：{links}")
            if m.source_session_ids:
                lines.append(f"- **来源 session**：`{', '.join(m.source_session_ids)}`")
            if m.decisions:
                lines.append("")
                lines.append("**关键决策：**")
                for d in m.decisions:
                    lines.append(f"- {d}")
            if m.action_items:
                lines.append("")
                lines.append("**待办事项：**")
                for it in m.action_items:
                    deadline = f"（截止 {it.deadline}）" if it.deadline else ""
                    lines.append(f"- [{it.owner}] {it.content} {deadline}".rstrip())
            lines.append("")

    if result.standalone_tasks:
        lines.append("## 二、独立待办")
        lines.append("")
        lines.append("| 负责人 | 任务 | 截止 | 上下文 | 置信度 |")
        lines.append("|---|---|---|---|---|")
        for it in result.standalone_tasks:
            ctx = (it.context or "").replace("|", "\\|")
            lines.append(
                f"| {it.owner} | {it.content} | {it.deadline or '—'} | {ctx or '—'} | "
                f"{it.confidence} |"
            )
        lines.append("")

    return "\n".join(lines)
