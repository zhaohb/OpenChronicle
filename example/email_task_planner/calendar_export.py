"""iCalendar export for email-derived tasks.

The exporter writes a simple RFC5545-compatible ``.ics`` file. It intentionally
does not call Outlook COM, Graph, or platform calendar APIs; importing the file
lets the user's calendar client own notifications and permissions.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from .extractor import EmailTask


def _fold_ical_line(line: str) -> list[str]:
    """Fold an iCalendar content line at roughly 75 octets.

    This implementation is character-based rather than byte-perfect, but keeps
    generated files readable and accepted by common desktop calendar clients.
    """
    if len(line) <= 75:
        return [line]
    chunks = [line[:75]]
    rest = line[75:]
    while rest:
        chunks.append(" " + rest[:74])
        rest = rest[74:]
    return chunks


def _escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", r"\;")
        .replace(",", r"\,")
        .replace("\r\n", r"\n")
        .replace("\n", r"\n")
    )


def _format_dt(value: datetime) -> str:
    # Floating local time: importing calendar uses the user's local timezone.
    return value.strftime("%Y%m%dT%H%M%S")


def _description(task: EmailTask) -> str:
    parts = [
        f"来源应用: {task.source_app}",
    ]
    if task.source_subject:
        parts.append(f"邮件/窗口标题: {task.source_subject}")
    if task.due_text:
        parts.append(f"原始时间表达: {task.due_text}")
    if task.source_session_ids:
        parts.append(f"OpenChronicle sessions: {', '.join(task.source_session_ids)}")
    if task.evidence:
        parts.append("证据:")
        parts.extend(f"- {ev}" for ev in task.evidence)
    return "\n".join(parts)


def _event_lines(task: EmailTask, remind_before_minutes: int) -> list[str]:
    if task.due_at is None:
        return []
    start = task.due_at
    end = start + timedelta(minutes=15)
    created = datetime.now()
    summary = f"邮件待办：{task.content[:80]}"
    lines = [
        "BEGIN:VEVENT",
        f"UID:{task.calendar_uid}",
        f"DTSTAMP:{_format_dt(created)}",
        f"DTSTART:{_format_dt(start)}",
        f"DTEND:{_format_dt(end)}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(_description(task))}",
        "BEGIN:VALARM",
        f"TRIGGER:-PT{max(0, remind_before_minutes)}M",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{_escape(summary)}",
        "END:VALARM",
        "END:VEVENT",
    ]
    folded: list[str] = []
    for line in lines:
        folded.extend(_fold_ical_line(line))
    return folded


def export_ics(
    tasks: list[EmailTask],
    path: Path,
    remind_before_minutes: int = 30,
) -> Path:
    """Write ``tasks`` with concrete deadlines to ``path`` and return it."""
    calendar_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//OpenChronicle//Email Task Planner//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for task in tasks:
        calendar_lines.extend(_event_lines(task, remind_before_minutes))
    calendar_lines.append("END:VCALENDAR")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\r\n".join(calendar_lines) + "\r\n", encoding="utf-8")
    return path


__all__ = ["export_ics"]
