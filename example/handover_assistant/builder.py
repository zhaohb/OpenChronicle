"""Build a handover document from OpenChronicle memory.

Pipeline:

1. List every active ``project-*.md`` in the local memory layer.
2. For each project, fetch its full content and produce a per-project status via LLM.
3. Pull tools / persons / orgs as supporting context.
4. Pull last-N-day event-daily entries as a "recent work" timeline.
5. Run a final LLM pass that synthesises the executive headline, top risks, and tools/systems.
6. Render to Markdown.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from shared import LLMClient, OCMCPClient
from shared.memory_loader import (
    Entry,
    MemoryFile,
    iter_event_entries,
    load_file_with_entries,
    load_memory_files,
)

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(slots=True)
class OpenTask:
    owner: str
    content: str
    deadline: str | None = None


@dataclass(slots=True)
class KeyContact:
    name: str
    role: str = "unknown"
    why: str = ""


@dataclass(slots=True)
class ToolEntry:
    name: str
    use_case: str = ""
    access_notes: str = ""


@dataclass(slots=True)
class RiskEntry:
    area: str
    risk: str
    mitigation: str = ""


@dataclass(slots=True)
class ProjectStatus:
    name: str
    path: str
    current_status: str = ""
    recent_decisions: list[str] = field(default_factory=list)
    open_tasks: list[OpenTask] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    key_contacts: list[KeyContact] = field(default_factory=list)
    related_documents: list[str] = field(default_factory=list)
    next_handover_steps: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HandoverDoc:
    generated_at: datetime
    since: date
    until: date
    headline: str = ""
    projects: list[ProjectStatus] = field(default_factory=list)
    top_risks: list[RiskEntry] = field(default_factory=list)
    must_know_facts: list[str] = field(default_factory=list)
    recent_work_narrative: str = ""
    tools_and_systems: list[ToolEntry] = field(default_factory=list)
    key_contacts_global: list[KeyContact] = field(default_factory=list)
    timeline: list[Entry] = field(default_factory=list)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _project_name(path: str) -> str:
    if path.startswith("project-") and path.endswith(".md"):
        return path[len("project-") : -len(".md")].replace("-", " ")
    return path


def _format_memory_file(file: MemoryFile) -> str:
    """Render a memory file's frontmatter + entries for prompt consumption."""
    lines = [f"path: {file.path}", f"description: {file.description}"]
    if file.tags:
        lines.append(f"tags: {', '.join(file.tags)}")
    lines.append("---")
    for entry in file.entries:
        ts = entry.timestamp.isoformat() if entry.timestamp != datetime.min else "?"
        tag_str = " ".join(f"#{t}" for t in entry.tags) if entry.tags else ""
        marker = " (superseded)" if entry.superseded_by else ""
        lines.append(f"- [{ts}] {tag_str}{marker}")
        for body_line in entry.body.splitlines():
            if body_line.strip():
                lines.append(f"    {body_line.rstrip()}")
    return "\n".join(lines)


def _format_timeline(entries: list[Entry]) -> str:
    lines: list[str] = []
    for entry in entries:
        ts = entry.timestamp.strftime("%Y-%m-%d %H:%M")
        first_body = entry.body.splitlines()[0] if entry.body else ""
        lines.append(f"- [{ts}] ({entry.path}) {first_body}")
    return "\n".join(lines)


def _coerce_open_tasks(raw: Any) -> list[OpenTask]:
    out: list[OpenTask] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        out.append(
            OpenTask(
                owner=str(item.get("owner", "me")),
                content=content,
                deadline=item.get("deadline"),
            )
        )
    return out


def _coerce_contacts(raw: Any) -> list[KeyContact]:
    out: list[KeyContact] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        out.append(
            KeyContact(
                name=name,
                role=str(item.get("role", "unknown")),
                why=str(item.get("why", "")).strip(),
            )
        )
    return out


def _coerce_tools(raw: Any) -> list[ToolEntry]:
    out: list[ToolEntry] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        out.append(
            ToolEntry(
                name=name,
                use_case=str(item.get("use_case", "")).strip(),
                access_notes=str(item.get("access_notes", "")).strip(),
            )
        )
    return out


def _coerce_risks(raw: Any) -> list[RiskEntry]:
    out: list[RiskEntry] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        risk = str(item.get("risk", "")).strip()
        if not risk:
            continue
        out.append(
            RiskEntry(
                area=str(item.get("area", "")),
                risk=risk,
                mitigation=str(item.get("mitigation", "")).strip(),
            )
        )
    return out


async def _summarize_project(
    llm: LLMClient,
    project_file: MemoryFile,
    recent_event_snippets: str,
) -> ProjectStatus:
    user_payload = (
        f"PROJECT MEMORY FILE\n{_format_memory_file(project_file)}\n\n"
        f"RECENT ACTIVITY SNIPPETS (last 30 days)\n{recent_event_snippets or '(none)'}"
        "\n\nReturn the JSON now."
    )
    response = llm.chat(
        system=_load_prompt("project_status.md"),
        user=user_payload,
        json_mode=True,
    )
    if not isinstance(response, dict):
        response = {}

    return ProjectStatus(
        name=str(response.get("name") or _project_name(project_file.path)),
        path=project_file.path,
        current_status=str(response.get("current_status", "")).strip(),
        recent_decisions=[str(x).strip() for x in response.get("recent_decisions", []) if x],
        open_tasks=_coerce_open_tasks(response.get("open_tasks")),
        blockers=[str(x).strip() for x in response.get("blockers", []) if x],
        key_contacts=_coerce_contacts(response.get("key_contacts")),
        related_documents=[str(x).strip() for x in response.get("related_documents", []) if x],
        next_handover_steps=[
            str(x).strip() for x in response.get("next_handover_steps", []) if x
        ],
    )


async def _gather_event_snippets(
    mcp: OCMCPClient,
    since: date,
    until: date,
) -> tuple[list[Entry], str]:
    entries: list[Entry] = []
    async for entry in iter_event_entries(mcp, since, until):
        entries.append(entry)
    text = _format_timeline(entries)
    return entries, text


async def _gather_supporting_files(
    mcp: OCMCPClient,
    prefixes: tuple[str, ...] = ("tool-", "person-", "org-"),
) -> dict[str, list[MemoryFile]]:
    out: dict[str, list[MemoryFile]] = {p: [] for p in prefixes}
    files = await load_memory_files(mcp, prefixes=prefixes)
    for f in files:
        loaded = await load_file_with_entries(mcp, f.path, tail_n=20)
        for prefix in prefixes:
            if f.path.startswith(prefix):
                out[prefix].append(loaded)
                break
    return out


async def build_handover(
    mcp: OCMCPClient,
    llm: LLMClient,
    since: date,
    until: date,
    project_filter: str | None = None,
) -> HandoverDoc:
    """Run the full handover pipeline for the given window."""
    doc = HandoverDoc(generated_at=datetime.now(), since=since, until=until)

    project_files = await load_memory_files(mcp, prefixes=("project-",))
    if project_filter:
        target_path = (
            project_filter
            if project_filter.startswith("project-")
            else f"project-{project_filter}.md"
        )
        project_files = [f for f in project_files if f.path == target_path]
        if not project_files:
            logger.warning("No project memory matched %r", project_filter)

    timeline_entries, timeline_text = await _gather_event_snippets(mcp, since, until)
    doc.timeline = timeline_entries

    project_statuses: list[ProjectStatus] = []
    for pf in project_files:
        full = await load_file_with_entries(mcp, pf.path)
        if not full.entries and (pf.updated is None or pf.updated.date() < since):
            continue
        status = await _summarize_project(llm, full, timeline_text)
        project_statuses.append(status)
    doc.projects = project_statuses

    supporting = await _gather_supporting_files(mcp)

    final_payload_lines: list[str] = []
    final_payload_lines.append("PROJECT STATUSES")
    for s in project_statuses:
        final_payload_lines.append(f"\n## {s.name} ({s.path})")
        if s.current_status:
            final_payload_lines.append(f"status: {s.current_status}")
        if s.blockers:
            final_payload_lines.append("blockers:")
            for b in s.blockers:
                final_payload_lines.append(f"  - {b}")
        if s.open_tasks:
            final_payload_lines.append("open_tasks:")
            for t in s.open_tasks:
                final_payload_lines.append(
                    f"  - [{t.owner}] {t.content} (deadline={t.deadline or 'n/a'})"
                )

    final_payload_lines.append("\nRECENT WORK TIMELINE")
    final_payload_lines.append(timeline_text or "(empty)")

    for prefix, files in supporting.items():
        if not files:
            continue
        final_payload_lines.append(f"\nSUPPORTING {prefix}* FILES")
        for f in files:
            final_payload_lines.append(_format_memory_file(f))

    final_response = llm.chat(
        system=_load_prompt("handover_doc.md"),
        user="\n".join(final_payload_lines) + "\n\nReturn the JSON now.",
        json_mode=True,
    )
    if isinstance(final_response, dict):
        doc.headline = str(final_response.get("headline", "")).strip()
        doc.recent_work_narrative = str(final_response.get("recent_work_narrative", "")).strip()
        doc.must_know_facts = [
            str(x).strip() for x in final_response.get("must_know_facts", []) if x
        ]
        doc.top_risks = _coerce_risks(final_response.get("top_risks"))
        doc.tools_and_systems = _coerce_tools(final_response.get("tools_and_systems"))

    seen: set[str] = set()
    contacts: list[KeyContact] = []
    for s in project_statuses:
        for c in s.key_contacts:
            if c.name not in seen:
                contacts.append(c)
                seen.add(c.name)
    doc.key_contacts_global = contacts[:10]

    return doc


def render_markdown(doc: HandoverDoc, owner_label: str = "我") -> str:
    """Render the handover document to Markdown."""
    lines: list[str] = []
    lines.append(f"# 工作交接文档 · {owner_label}")
    lines.append("")
    lines.append(
        f"_生成时间：{doc.generated_at.strftime('%Y-%m-%d %H:%M')}　"
        f"覆盖范围：{doc.since.isoformat()} ~ {doc.until.isoformat()}_"
    )
    lines.append("")

    if doc.headline:
        lines.append("> " + doc.headline.replace("\n", " "))
        lines.append("")

    if doc.must_know_facts:
        lines.append("## 0. 接手前必须知道的事")
        lines.append("")
        for f in doc.must_know_facts:
            lines.append(f"- {f}")
        lines.append("")

    lines.append("## 一、当前负责的项目")
    lines.append("")
    if not doc.projects:
        lines.append("> 在指定窗口内未找到活动项目。")
        lines.append("")
    for p in doc.projects:
        lines.append(f"### {p.name}")
        lines.append("")
        lines.append(f"- **记忆文件**：`{p.path}`")
        if p.current_status:
            lines.append(f"- **当前状态**：{p.current_status}")
        if p.recent_decisions:
            lines.append("- **最近决策**：")
            for d in p.recent_decisions:
                lines.append(f"    - {d}")
        if p.open_tasks:
            lines.append("- **未完成任务**：")
            for t in p.open_tasks:
                deadline = f"（截止 {t.deadline}）" if t.deadline else ""
                lines.append(f"    - [{t.owner}] {t.content} {deadline}".rstrip())
        if p.blockers:
            lines.append("- **风险/阻塞**：")
            for b in p.blockers:
                lines.append(f"    - {b}")
        if p.key_contacts:
            lines.append("- **关键联系人**：")
            for c in p.key_contacts:
                lines.append(f"    - {c.name}（{c.role}）— {c.why}")
        if p.related_documents:
            lines.append("- **相关文档**：")
            for d in p.related_documents:
                lines.append(f"    - {d}")
        if p.next_handover_steps:
            lines.append("- **接手建议**：")
            for s in p.next_handover_steps:
                lines.append(f"    - {s}")
        lines.append("")

    lines.append("## 二、未完成事项汇总")
    lines.append("")
    rows: list[tuple[str, str, str, str]] = []
    for p in doc.projects:
        for t in p.open_tasks:
            rows.append((p.name, t.owner, t.content, t.deadline or "—"))
    if rows:
        lines.append("| 项目 | 负责人 | 任务 | 截止 |")
        lines.append("|---|---|---|---|")
        for proj, owner, task, dl in rows:
            lines.append(f"| {proj} | {owner} | {task} | {dl} |")
    else:
        lines.append("> 暂无未完成事项。")
    lines.append("")

    lines.append("## 三、关键联系人")
    lines.append("")
    if doc.key_contacts_global:
        lines.append("| 姓名 | 角色 | 关注点 |")
        lines.append("|---|---|---|")
        for c in doc.key_contacts_global:
            lines.append(f"| {c.name} | {c.role} | {c.why} |")
    else:
        lines.append("> 暂未识别出关键联系人。")
    lines.append("")

    lines.append("## 四、最近工作脉络")
    lines.append("")
    if doc.recent_work_narrative:
        lines.append(doc.recent_work_narrative)
        lines.append("")
    if doc.timeline:
        lines.append("<details><summary>展开原始时间线（按日期）</summary>")
        lines.append("")
        for entry in doc.timeline[-50:]:
            ts = entry.timestamp.strftime("%Y-%m-%d %H:%M")
            first_body = entry.body.splitlines()[0] if entry.body else ""
            lines.append(f"- [{ts}] `{entry.path}` {first_body}")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    lines.append("## 五、风险与阻塞")
    lines.append("")
    if doc.top_risks:
        lines.append("| 范围 | 风险 | 缓解措施 |")
        lines.append("|---|---|---|")
        for r in doc.top_risks:
            lines.append(f"| {r.area} | {r.risk} | {r.mitigation or 'TBD'} |")
    else:
        lines.append("> 暂未识别明显风险。")
    lines.append("")

    lines.append("## 六、常用工具与系统")
    lines.append("")
    if doc.tools_and_systems:
        lines.append("| 名称 | 用途 | 接入说明 |")
        lines.append("|---|---|---|")
        for t in doc.tools_and_systems:
            lines.append(f"| {t.name} | {t.use_case} | {t.access_notes or '—'} |")
    else:
        lines.append("> 未发现明确的工具/系统使用记录。")
    lines.append("")

    return "\n".join(lines)


__all__ = [
    "HandoverDoc",
    "ProjectStatus",
    "build_handover",
    "render_markdown",
]


# Convenience for ad-hoc invocation: ``python builder.py``
async def _demo() -> None:
    async with OCMCPClient() as mcp:
        doc = await build_handover(
            mcp,
            LLMClient(),
            since=date.today() - timedelta(days=30),
            until=date.today(),
        )
    print(render_markdown(doc))


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
