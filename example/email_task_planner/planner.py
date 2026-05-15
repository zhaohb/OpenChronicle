"""CLI entry point for the email task planner example.

Usage::

    python -m email_task_planner.planner
    python -m email_task_planner.planner --yesterday
    python -m email_task_planner.planner --since 2026-05-01 --until 2026-05-09
    python -m email_task_planner.planner --calendar-out ./mailtasks/tasks.ics
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import typer

from shared import LLMClient, LLMConfig, OCMCPClient

from .calendar_export import export_ics
from .extractor import extract_email_tasks, render_markdown

app = typer.Typer(
    add_completion=False,
    help="Extract email tasks from OpenChronicle memory and export calendar reminders.",
)


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter(f"Date must be YYYY-MM-DD (got {value!r})") from exc


def _parse_time(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise typer.BadParameter(f"Time must be HH:MM (got {value!r})") from exc


def _resolve_range(
    date_: str | None,
    since: str | None,
    until: str | None,
) -> tuple[date, date]:
    if date_ is not None and (since is not None or until is not None):
        raise typer.BadParameter("Use --date OR (--since/--until), not both.")
    if date_ is not None:
        d = _parse_date(date_) or date.today()
        return d, d
    today = date.today()
    s = _parse_date(since) or today
    u = _parse_date(until) or today
    if s > u:
        raise typer.BadParameter("--since must be <= --until")
    return s, u


async def _run(
    since: date,
    until: date,
    output: Path,
    calendar_out: Path,
    mcp_url: str | None,
    model: str | None,
    remind_before: int,
    default_due_time: time,
) -> tuple[Path, Path]:
    llm_config = LLMConfig()
    if model:
        llm_config.model = model

    client = OCMCPClient(url=mcp_url) if mcp_url else OCMCPClient()
    async with client as mcp:
        result = await extract_email_tasks(
            mcp,
            LLMClient(llm_config),
            since,
            until,
            default_due_time=default_due_time,
        )

    export_ics(result.tasks, calendar_out, remind_before_minutes=remind_before)
    md = render_markdown(result, calendar_path=calendar_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    return output, calendar_out


@app.command()
def main(
    date_: str = typer.Option(None, "--date", help="Single day, YYYY-MM-DD."),
    since: str = typer.Option(None, "--since", help="Start date, YYYY-MM-DD."),
    until: str = typer.Option(None, "--until", help="End date, YYYY-MM-DD."),
    output: Path = typer.Option(None, "--output", "-o", help="Output Markdown file."),
    calendar_out: Path = typer.Option(None, "--calendar-out", help="Output .ics calendar file."),
    remind_before: int = typer.Option(
        30,
        "--remind-before",
        help="Calendar alarm offset in minutes before due_at.",
    ),
    default_due_time: str = typer.Option(
        "09:00",
        "--default-due-time",
        help="Time used when the email only contains a due date, HH:MM.",
    ),
    mcp_url: str = typer.Option(None, "--mcp-url", help="Override OpenChronicle MCP URL."),
    model: str = typer.Option(None, "--model", help="Override LLM model (e.g. qwen2.5:14b)."),
    yesterday: bool = typer.Option(False, "--yesterday", help="Shortcut for --date <yesterday>."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """Extract email tasks and create importable calendar reminders."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if yesterday:
        if date_ or since or until:
            raise typer.BadParameter("--yesterday cannot combine with date options.")
        date_ = (date.today() - timedelta(days=1)).isoformat()

    s, u = _resolve_range(date_, since, until)
    suffix = s.isoformat() if s == u else f"{s.isoformat()}_to_{u.isoformat()}"
    if output is None:
        output = Path("mailtasks") / f"email-tasks-{suffix}.md"
    if calendar_out is None:
        calendar_out = Path("mailtasks") / f"email-tasks-{suffix}.ics"
    due_time = _parse_time(default_due_time)

    try:
        md_path, ics_path = asyncio.run(
            _run(
                s,
                u,
                output,
                calendar_out,
                mcp_url,
                model,
                remind_before,
                due_time,
            )
        )
    except KeyboardInterrupt:
        sys.exit(130)

    typer.echo(f"Email task report written to {md_path}")
    typer.echo(f"Calendar reminders written to {ics_path}")


if __name__ == "__main__":
    app()
