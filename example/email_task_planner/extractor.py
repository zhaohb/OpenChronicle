"""Extract email-derived tasks from OpenChronicle event-daily entries.

This module deliberately avoids mailbox APIs. It only sees what OpenChronicle
already reduced into ``event-YYYY-MM-DD.md`` entries, then narrows those entries
to mail-like contexts before asking the local LLM for grounded tasks.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from dateutil import parser as dateparser

from shared import LLMClient, OCMCPClient
from shared.memory_loader import Entry, iter_event_entries

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

EMAIL_APP_KEYWORDS = (
    "outlook",
    "mail",
    "thunderbird",
    "foxmail",
    "gmail",
    "网易邮箱",
    "qq邮箱",
    "企业邮箱",
)

EMAIL_TEXT_KEYWORDS = (
    "mail",
    "email",
    "e-mail",
    "inbox",
    "outbox",
    "sent items",
    "outlook",
    "gmail",
    "邮件",
    "收件箱",
    "发件箱",
    "已发送",
    "回复",
    "转发",
    "主题",
    "subject",
    "from:",
    "to:",
)

TASK_TIME_KEYWORDS = (
    "deadline",
    "due",
    "before",
    "by ",
    "todo",
    "follow up",
    "action item",
    "截止",
    "到期",
    "之前",
    "前",
    "今天",
    "明天",
    "下周",
    "本周",
    "周一",
    "周二",
    "周三",
    "周四",
    "周五",
    "周六",
    "周日",
    "请在",
    "麻烦",
)


@dataclass(slots=True)
class EmailTask:
    owner: str
    content: str
    due_at: datetime | None = None
    due_text: str | None = None
    confidence: str = "medium"
    source_app: str = "unknown"
    source_subject: str = ""
    source_session_ids: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    calendar_uid: str = ""
    inferred_due_time: bool = False


@dataclass(slots=True)
class EmailTaskResult:
    since: date
    until: date
    tasks: list[EmailTask] = field(default_factory=list)
    unscheduled_tasks: list[EmailTask] = field(default_factory=list)
    entry_count: int = 0
    candidate_entry_count: int = 0


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _entry_text(entry: Entry) -> str:
    sid = entry.session_id or "?"
    ts = entry.timestamp.strftime("%Y-%m-%d %H:%M")
    tag_str = " ".join(f"#{t}" for t in entry.tags if not t.startswith("sid:"))
    lines = [f"- [{ts}] (session={sid}) {tag_str}".rstrip()]
    for line in entry.body.splitlines():
        if line.strip():
            lines.append(f"    {line.rstrip()}")
    return "\n".join(lines)


def _looks_email_related(entry: Entry) -> bool:
    text = (entry.body or "").lower()
    haystack = " ".join([entry.path or "", text] + entry.tags).lower()
    app_hit = any(k in haystack for k in EMAIL_APP_KEYWORDS)
    mail_hit = any(k in haystack for k in EMAIL_TEXT_KEYWORDS)
    task_hint = any(k in haystack for k in TASK_TIME_KEYWORDS)
    # Native mail apps are enough; browser activity needs mail-ish page evidence.
    if app_hit and (mail_hit or task_hint):
        return True
    if any(browser in haystack for browser in ("chrome", "edge", "msedge", "firefox")):
        return mail_hit and task_hint
    return mail_hit and task_hint


def _format_entries(entries: list[Entry]) -> str:
    if not entries:
        return "(no email-like entries)"
    by_day: dict[str, list[Entry]] = {}
    for entry in entries:
        day = entry.timestamp.date().isoformat()
        by_day.setdefault(day, []).append(entry)

    lines: list[str] = []
    for day in sorted(by_day):
        lines.append(f"\n## Day {day}\n")
        for entry in by_day[day]:
            lines.append(_entry_text(entry))
    return "\n".join(lines)


def _parse_due_at(value: Any, default_due_time: time) -> tuple[datetime | None, bool]:
    if value in (None, "", "null"):
        return None, False
    raw = str(value).strip()
    try:
        parsed = dateparser.isoparse(raw)
    except (TypeError, ValueError):
        try:
            parsed = dateparser.parse(raw)
        except (TypeError, ValueError, OverflowError):
            return None, False
    if parsed is None:
        return None, False
    inferred = False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        parsed = datetime.combine(parsed.date(), default_due_time)
        inferred = True
    return parsed.replace(tzinfo=None), inferred


def _make_uid(content: str, due_at: datetime | None, sessions: list[str]) -> str:
    basis = "|".join(
        [
            ",".join(sorted(sessions)),
            content.strip().lower(),
            due_at.isoformat() if due_at else "",
        ]
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
    return f"oc-mailtask-{digest}@openchronicle.local"


def _coerce_task(raw: Any, default_due_time: time) -> EmailTask | None:
    if not isinstance(raw, dict):
        return None
    content = str(raw.get("content", "")).strip()
    if not content:
        return None

    due_at, inferred = _parse_due_at(raw.get("due_at"), default_due_time)
    sessions = [str(x).strip() for x in (raw.get("source_session_ids") or []) if x]
    evidence = [str(x).strip() for x in (raw.get("evidence") or []) if x]
    confidence = str(raw.get("confidence", "medium")).strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    task = EmailTask(
        owner=str(raw.get("owner", "me")).strip() or "me",
        content=content,
        due_at=due_at,
        due_text=str(raw.get("due_text", "")).strip() or None,
        confidence=confidence,
        source_app=str(raw.get("source_app", "unknown")).strip() or "unknown",
        source_subject=str(raw.get("source_subject", "")).strip(),
        source_session_ids=sessions,
        evidence=evidence[:3],
        inferred_due_time=inferred,
    )
    task.calendar_uid = str(raw.get("calendar_uid", "")).strip() or _make_uid(
        task.content, task.due_at, task.source_session_ids
    )
    return task


def _dedupe_tasks(tasks: list[EmailTask]) -> list[EmailTask]:
    out: list[EmailTask] = []
    seen: set[tuple[str, str, str]] = set()
    for task in tasks:
        key = (
            task.owner.lower(),
            task.content.lower(),
            task.due_at.isoformat() if task.due_at else task.due_text or "",
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(task)
    return out


async def extract_email_tasks(
    mcp: OCMCPClient,
    llm: LLMClient,
    since: date,
    until: date,
    default_due_time: time,
) -> EmailTaskResult:
    """Extract actionable email tasks in ``[since, until]``."""
    entries: list[Entry] = []
    async for entry in iter_event_entries(mcp, since, until):
        entries.append(entry)

    candidates = [entry for entry in entries if _looks_email_related(entry)]
    result = EmailTaskResult(
        since=since,
        until=until,
        entry_count=len(entries),
        candidate_entry_count=len(candidates),
    )
    if not candidates:
        logger.warning("No email-like event-daily entries in [%s, %s]", since, until)
        return result

    rendered = _format_entries(candidates)
    response = llm.chat(
        system=_load_prompt("email_task_extract.md"),
        user=(
            f"Window: {since.isoformat()} to {until.isoformat()}\n"
            f"Default due time for date-only deadlines: {default_due_time.strftime('%H:%M')}\n\n"
            f"Here are email-like event-daily entries:\n{rendered}\n\nReturn the JSON now."
        ),
        json_mode=True,
    )
    if not isinstance(response, dict):
        return result

    parsed = [
        task
        for item in (response.get("tasks") or [])
        if (task := _coerce_task(item, default_due_time)) is not None
    ]
    parsed = _dedupe_tasks(parsed)
    result.tasks = [task for task in parsed if task.due_at is not None]
    result.unscheduled_tasks = [task for task in parsed if task.due_at is None]
    return result


def _render_task_lines(task: EmailTask) -> list[str]:
    lines = [
        f"- **负责人**：{task.owner}",
        f"- **内容**：{task.content}",
        f"- **来源应用**：{task.source_app}",
    ]
    if task.source_subject:
        lines.append(f"- **邮件/窗口标题**：{task.source_subject}")
    if task.due_at:
        inferred = "（时间由默认值补齐）" if task.inferred_due_time else ""
        lines.append(f"- **时间节点**：{task.due_at.strftime('%Y-%m-%d %H:%M')}{inferred}")
    elif task.due_text:
        lines.append(f"- **原始时间表达**：{task.due_text}")
    else:
        lines.append("- **时间节点**：未识别")
    lines.append(f"- **置信度**：{task.confidence}")
    if task.source_session_ids:
        lines.append(f"- **来源 session**：`{', '.join(task.source_session_ids)}`")
    if task.evidence:
        lines.append("- **证据**：")
        for ev in task.evidence:
            lines.append(f"  - {ev}")
    return lines


def _escape_table(value: str) -> str:
    return value.replace("|", r"\|")


def render_markdown(result: EmailTaskResult, calendar_path: Path | None = None) -> str:
    """Render extraction results to a Markdown report."""
    title_range = (
        result.since.isoformat()
        if result.since == result.until
        else f"{result.since.isoformat()} ~ {result.until.isoformat()}"
    )
    lines: list[str] = [
        f"# 邮件待办与时间节点 · {title_range}",
        "",
        (
            f"_来源：OpenChronicle event-daily entries（共 {result.entry_count} 条；"
            f"邮件候选 {result.candidate_entry_count} 条），生成时间 "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}_"
        ),
        "",
    ]
    if calendar_path is not None:
        lines.append(f"_日历文件：`{calendar_path}`_")
        lines.append("")

    if not result.tasks and not result.unscheduled_tasks:
        lines.append("> 没有从已捕获的邮件上下文中识别出可执行待办。")
        lines.append("")
        lines.append(
            "> 注意：本应用不读取邮箱账号，只分析 OpenChronicle 已捕获并写入记忆的邮件窗口/草稿内容。"
        )
        return "\n".join(lines)

    if result.tasks:
        lines.append("## 一、有时间节点的邮件待办")
        lines.append("")
        lines.append("| 时间节点 | 负责人 | 待办 | 来源 | 置信度 |")
        lines.append("|---|---|---|---|---|")
        for task in result.tasks:
            due = task.due_at.strftime("%Y-%m-%d %H:%M") if task.due_at else "—"
            source = task.source_subject or task.source_app
            lines.append(
                f"| {due} | {task.owner} | {_escape_table(task.content)} | "
                f"{_escape_table(source)} | {task.confidence} |"
            )
        lines.append("")
        for idx, task in enumerate(result.tasks, start=1):
            lines.append(f"### {idx}. {task.content[:60]}")
            lines.append("")
            lines.extend(_render_task_lines(task))
            lines.append("")

    if result.unscheduled_tasks:
        lines.append("## 二、无明确时间节点的邮件待办")
        lines.append("")
        for idx, task in enumerate(result.unscheduled_tasks, start=1):
            lines.append(f"### {idx}. {task.content[:60]}")
            lines.append("")
            lines.extend(_render_task_lines(task))
            lines.append("")

    lines.append("## 三、能力边界")
    lines.append("")
    lines.append("- 本应用不接 IMAP / Graph / Gmail API，不读取完整邮箱。")
    lines.append("- 只有出现在桌面并被 OpenChronicle reducer 写入 `event-*.md` 的内容才会被分析。")
    lines.append("- `.ics` 文件需要导入 Outlook / Windows Calendar / Apple Calendar 后才会触发提醒。")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "EmailTask",
    "EmailTaskResult",
    "extract_email_tasks",
    "render_markdown",
]
