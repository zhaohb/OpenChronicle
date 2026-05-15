"""CLI entry point for the handover assistant example.

Usage::

    python -m handover_assistant.handover                              # last 30 days
    python -m handover_assistant.handover --since 2026-04-01
    python -m handover_assistant.handover --project openchronicle
    python -m handover_assistant.handover --output ./out/handover.md
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import typer

from shared import LLMClient, LLMConfig, OCMCPClient

from .builder import build_handover, render_markdown

app = typer.Typer(
    add_completion=False,
    help="Generate a leaver/transfer handover document from OpenChronicle memory.",
)


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter(f"Date must be YYYY-MM-DD (got {value!r})") from exc


@app.command()
def main(
    since: str = typer.Option(None, "--since", help="Start date (YYYY-MM-DD)."),
    until: str = typer.Option(None, "--until", help="End date (YYYY-MM-DD), default today."),
    days: int = typer.Option(30, "--days", help="Look-back days when --since is omitted."),
    project: str = typer.Option(
        None,
        "--project",
        help="Limit to a single project (name or full `project-<name>.md` path).",
    ),
    owner_label: str = typer.Option("我", "--owner", help="Label used in the document title."),
    output: Path = typer.Option(None, "--output", "-o", help="Output Markdown file."),
    mcp_url: str = typer.Option(None, "--mcp-url", help="Override OpenChronicle MCP URL."),
    model: str = typer.Option(None, "--model", help="Override LLM model (e.g. qwen2.5:14b)."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """Build a handover document covering the given time window."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    today = date.today()
    s = _parse_date(since) or (today - timedelta(days=days))
    u = _parse_date(until) or today
    if s > u:
        raise typer.BadParameter("--since must be <= --until")

    if output is None:
        suffix = today.isoformat()
        if project:
            slug = project.replace("project-", "").replace(".md", "").replace("/", "_")
            filename = f"handover-{suffix}-{slug}.md"
        else:
            filename = f"handover-{suffix}.md"
        output = Path("handover") / filename

    llm_config = LLMConfig()
    if model:
        llm_config.model = model

    async def _run() -> Path:
        client = OCMCPClient(url=mcp_url) if mcp_url else OCMCPClient()
        async with client as mcp:
            doc = await build_handover(mcp, LLMClient(llm_config), s, u, project_filter=project)
        md = render_markdown(doc, owner_label=owner_label)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(md, encoding="utf-8")
        return output

    try:
        result_path = asyncio.run(_run())
    except KeyboardInterrupt:
        sys.exit(130)

    typer.echo(f"Handover document written to {result_path}")


if __name__ == "__main__":
    app()
