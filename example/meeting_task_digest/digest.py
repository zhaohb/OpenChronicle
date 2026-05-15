"""CLI entry point for the meeting + task digest example.

Usage::

    python -m meeting_task_digest.digest                       # today
    python -m meeting_task_digest.digest --date 2026-05-08
    python -m meeting_task_digest.digest --since 2026-05-01 --until 2026-05-08
    python -m meeting_task_digest.digest --output ./out/digest.md
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import typer

from shared import LLMClient, LLMConfig, OCMCPClient

from .extractor import extract_digest, render_markdown

app = typer.Typer(add_completion=False, help="Generate a meeting & task digest from OpenChronicle memory.")


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter(f"Date must be YYYY-MM-DD (got {value!r})") from exc


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
    mcp_url: str | None,
    model: str | None,
) -> Path:
    llm_config = LLMConfig()
    if model:
        llm_config.model = model

    client = OCMCPClient(url=mcp_url) if mcp_url else OCMCPClient()
    async with client as mcp:
        result = await extract_digest(mcp, LLMClient(llm_config), since, until)

    md = render_markdown(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")
    return output


@app.command()
def main(
    date_: str = typer.Option(None, "--date", help="Single day, YYYY-MM-DD."),
    since: str = typer.Option(None, "--since", help="Start date, YYYY-MM-DD."),
    until: str = typer.Option(None, "--until", help="End date, YYYY-MM-DD."),
    output: Path = typer.Option(None, "--output", "-o", help="Output Markdown file."),
    mcp_url: str = typer.Option(None, "--mcp-url", help="Override OpenChronicle MCP URL."),
    model: str = typer.Option(None, "--model", help="Override LLM model (e.g. qwen2.5:14b)."),
    yesterday: bool = typer.Option(False, "--yesterday", help="Shortcut for --date <yesterday>."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """Generate a meeting & task digest from OpenChronicle's local memory."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if yesterday:
        if date_ or since or until:
            raise typer.BadParameter("--yesterday cannot combine with date options.")
        date_ = (date.today() - timedelta(days=1)).isoformat()

    s, u = _resolve_range(date_, since, until)
    if output is None:
        suffix = s.isoformat() if s == u else f"{s.isoformat()}_to_{u.isoformat()}"
        output = Path("digests") / f"meeting-{suffix}.md"

    try:
        result_path = asyncio.run(_run(s, u, output, mcp_url, model))
    except KeyboardInterrupt:
        sys.exit(130)

    typer.echo(f"Digest written to {result_path}")


if __name__ == "__main__":
    app()
