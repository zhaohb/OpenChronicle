"""CLI + orchestration for the Activity Chronicler example.

Usage::

    # This (ISO) week's recap, written to ./recaps/week-2026-W19.md
    python -m activity_chronicler.chronicler

    # An explicit ISO week, no previous-window comparison
    python -m activity_chronicler.chronicler --week 2026-W19 --no-compare-previous

    # This calendar month
    python -m activity_chronicler.chronicler --month 2026-05

    # Arbitrary range
    python -m activity_chronicler.chronicler --since 2026-04-15 --until 2026-04-30

    # Override LLM model / MCP URL / output path
    python -m activity_chronicler.chronicler --model qwen2.5:14b --output ./recaps/weekly.md
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import typer

from shared import LLMClient, LLMConfig, OCMCPClient
from shared.memory_loader import (
    Entry,
    MemoryFile,
    iter_event_entries,
    load_file_with_entries,
    load_memory_files,
)

from .stats import ActivityStats, build_compact_timeline_lines, compute_stats, parse_event_entries
from .synthesizer import (
    ChangeItem,
    NotableOneOff,
    OpenThread,
    Recap,
    Theme,
    is_mail_meeting_notable_one_off,
    synthesize_recap,
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    add_completion=False,
    help="Generate a long-term desktop-activity recap from OpenChronicle memory.",
)


# ---------------------------------------------------------------------------
# Date-range resolution
# ---------------------------------------------------------------------------

_ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{1,2})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter(f"Date must be YYYY-MM-DD (got {value!r})") from exc


def _iso_week_range(label: str) -> tuple[date, date]:
    m = _ISO_WEEK_RE.match(label)
    if not m:
        raise typer.BadParameter(f"--week must look like 2026-W19 (got {label!r})")
    year = int(m.group(1))
    week = int(m.group(2))
    try:
        monday = date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid ISO week {label!r}: {exc}") from exc
    sunday = monday + timedelta(days=6)
    return monday, sunday


def _month_range(label: str) -> tuple[date, date]:
    m = _MONTH_RE.match(label)
    if not m:
        raise typer.BadParameter(f"--month must look like 2026-05 (got {label!r})")
    year = int(m.group(1))
    month = int(m.group(2))
    if not 1 <= month <= 12:
        raise typer.BadParameter(f"--month month must be 1-12 (got {month})")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _resolve_window(
    week: str | None,
    month: str | None,
    since: str | None,
    until: str | None,
) -> tuple[date, date, str]:
    """Return ``(since, until, window_label)``."""
    chosen = [x for x in (week, month, (since or until)) if x]
    if len(chosen) > 1:
        raise typer.BadParameter("Use only one of --week / --month / (--since/--until).")
    if week:
        s, u = _iso_week_range(week)
        return s, u, f"ISO week {week}"
    if month:
        s, u = _month_range(month)
        return s, u, f"month {month}"
    if since or until:
        today = date.today()
        s = _parse_date(since) or today
        u = _parse_date(until) or today
        if s > u:
            raise typer.BadParameter("--since must be <= --until")
        return s, u, f"{s.isoformat()} → {u.isoformat()}"
    # Default = current ISO week.
    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()
    s, u = _iso_week_range(f"{iso_year}-W{iso_week:02d}")
    return s, u, f"ISO week {iso_year}-W{iso_week:02d}"


def _previous_window(since: date, until: date) -> tuple[date, date]:
    """Return the immediately preceding window of the same length."""
    span = (until - since).days + 1
    prev_until = since - timedelta(days=1)
    prev_since = prev_until - timedelta(days=span - 1)
    return prev_since, prev_until


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def _gather_event_entries(
    mcp: OCMCPClient, since: date, until: date
) -> list[Entry]:
    out: list[Entry] = []
    async for entry in iter_event_entries(mcp, since, until):
        out.append(entry)
    return out


async def _gather_durable_context(mcp: OCMCPClient) -> list[MemoryFile]:
    """Pull a small bundle of `project-`, `topic-`, `tool-`, `user-` files for naming hints."""
    files = await load_memory_files(
        mcp,
        prefixes=("project-", "topic-", "tool-", "user-"),
    )
    enriched: list[MemoryFile] = []
    # Cap to keep the prompt small; we want descriptions, not full bodies here.
    for f in files[:30]:
        try:
            full = await load_file_with_entries(mcp, f.path, tail_n=5)
        except Exception as exc:
            logger.debug("Skipping durable file %s: %s", f.path, exc)
            continue
        enriched.append(full)
    return enriched


async def build_recap(
    mcp: OCMCPClient,
    llm: LLMClient,
    since: date,
    until: date,
    window_label: str,
    compare_previous: bool = True,
    previous_state: Recap | None = None,
) -> Recap:
    """Run the full pipeline for one window."""
    entries = await _gather_event_entries(mcp, since, until)
    sub_tasks = parse_event_entries(entries)
    stats = compute_stats(sub_tasks, since=since, until=until)

    durable = await _gather_durable_context(mcp)

    # Build a previous-window stats snapshot when requested. We only run the
    # cheap deterministic pass for the previous window; we do NOT call the LLM
    # on history every time. If the caller passed a previously-saved Recap, we
    # use that directly.
    previous_recap = previous_state
    if compare_previous and previous_recap is None:
        prev_since, prev_until = _previous_window(since, until)
        prev_entries = await _gather_event_entries(mcp, prev_since, prev_until)
        prev_sub_tasks = parse_event_entries(prev_entries)
        prev_stats = compute_stats(prev_sub_tasks, since=prev_since, until=prev_until)
        if prev_stats.total_minutes > 0:
            previous_recap = Recap(
                since=prev_since,
                until=prev_until,
                window_label=f"previous {window_label}",
                generated_at=datetime.now(),
                stats=prev_stats,
            )

    return synthesize_recap(
        llm=llm,
        stats=stats,
        sub_tasks=sub_tasks,
        event_entries=entries,
        durable=durable,
        previous=previous_recap,
        window_label=window_label,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

_KIND_LABEL = {
    "new_theme": "New theme",
    "dropped_theme": "Dropped theme",
    "app_shift": "App mix shift",
    "tempo_shift": "Tempo shift",
}


def _render_change_table(items: Iterable[ChangeItem]) -> list[str]:
    rows: list[str] = []
    items = list(items)
    if not items:
        return rows
    rows.append("| Category | Note |")
    rows.append("|---|---|")
    for c in items:
        label = _KIND_LABEL.get(c.kind, c.kind)
        rows.append(f"| {label} | {c.note} |")
    return rows


def _render_top_apps(stats: ActivityStats, n: int = 8) -> list[str]:
    rows: list[str] = []
    top = stats.top_apps(n)
    if not top:
        return rows
    rows.append("| App | Time (min) | Share |")
    rows.append("|---|---|---|")
    for app_name, mins in top:
        rows.append(f"| {app_name} | {mins} | {stats.percent(mins)}% |")
    return rows


def _format_minutes(mins: int) -> str:
    if mins < 60:
        return f"{mins} min"
    h = mins // 60
    m = mins % 60
    return f"{h} hr {m} min" if m else f"{h} hr"


def _render_open_thread_markdown(ot: OpenThread) -> list[str]:
    """Structured last-known-state, or legacy one-line string."""
    if ot.is_legacy_flat_sentence():
        return [f"- {ot.last_snapshot.strip()}"]

    title = ot.topic.strip() or "Untitled thread"
    lines: list[str] = [f"- **{title}**"]
    lines.append(f"  - Last seen: {ot.last_seen.strip() or '—'}")
    lines.append(f"  - Last status: {ot.last_status.strip() or '—'}")
    snap = ot.last_snapshot.strip()
    if snap:
        lines.append("  - Excerpt / verbatim:")
        for ln in snap.splitlines() or [snap]:
            lines.append(f"    {ln}")
    else:
        lines.append("  - Excerpt / verbatim: —")
    lines.append(f"  - Why unfinished: {ot.why_unfinished.strip() or '—'}")
    if ot.grounded_in.strip():
        lines.append(f"  - Evidence: {ot.grounded_in.strip()}")
    return lines


def render_markdown(recap: Recap, owner_label: str = "Me") -> str:
    """Render a :class:`Recap` to a long-term-memory Markdown artifact."""
    s = recap.stats
    lines: list[str] = []
    lines.append(f"# Desktop Activity Recap · {owner_label}")
    lines.append("")
    lines.append(
        f"_Window: {recap.window_label} ({recap.since.isoformat()} → {recap.until.isoformat()})  "
        f"Generated: {recap.generated_at.strftime('%Y-%m-%d %H:%M')}_"
    )
    lines.append("")

    if recap.headline:
        lines.append("> " + recap.headline.replace("\n", " "))
        lines.append("")

    lines.append("## 1. Window Overview")
    lines.append("")
    lines.append(
        f"- **Tracked activity time**: {_format_minutes(s.total_minutes)} ({s.sub_task_count} sub_tasks)"
    )
    if recap.coverage_minutes:
        lines.append(
            f"- **Theme coverage**: {_format_minutes(recap.coverage_minutes)} "
            f"(about {s.percent(recap.coverage_minutes)}% covered by explicit themes)"
        )
    if recap.coverage_note:
        lines.append(f"- **Coverage note**: {recap.coverage_note}")
    if recap.time_breakdown_note:
        lines.append(f"- **Time-distribution note**: {recap.time_breakdown_note}")
    lines.append("")

    tl = build_compact_timeline_lines(s)
    if tl:
        lines.append("## 2. Activity Timeline")
        lines.append("")
        lines.append(
            "_Built from `event-*.md` sub_tasks. Original granularity is preserved when the "
            "timeline is short enough; only dense windows are merged with an adaptive same-day / "
            "same-app / same-context gap threshold to keep the list compact._"
        )
        lines.append("")
        lines.extend(tl)
        lines.append("")

    if recap.summary:
        lines.append("## 3. Narrative")
        lines.append("")
        lines.append(recap.summary)
        lines.append("")

    lines.append("## 4. Theme Distribution")
    lines.append("")
    if recap.themes:
        for t in recap.themes:
            lines.append(f"### {t.name}")
            lines.append("")
            meta_bits = [f"about {_format_minutes(t.approx_minutes)}"]
            if t.apps:
                meta_bits.append(" / ".join(t.apps))
            lines.append("_" + " · ".join(meta_bits) + "_")
            lines.append("")
            if t.description:
                lines.append(t.description)
                lines.append("")
            if t.evidence_ranges:
                lines.append("<details><summary>Evidence ranges</summary>")
                lines.append("")
                for r in t.evidence_ranges:
                    lines.append(f"- `{r}`")
                lines.append("")
                lines.append("</details>")
                lines.append("")
    else:
        lines.append("> No valid themes were clustered for this window.")
        lines.append("")

    mail_meeting = [
        o for o in recap.notable_one_offs if is_mail_meeting_notable_one_off(o)
    ]
    if mail_meeting:
        lines.append("## 5. Mail / Calendar / Meeting Signals")
        lines.append("")
        lines.append(
            "> Only communication-related fragments that may need follow-up or later review are shown here; "
            "routine file browsing, transient input, and topic-less one-offs are hidden."
        )
        lines.append("")
        for o in mail_meeting:
            lines.append(f"- `{o.range}` — {o.note}")
        lines.append("")

    lines.append("## 6. Top Apps")
    lines.append("")
    rows = _render_top_apps(s)
    if rows:
        lines.extend(rows)
    else:
        lines.append("> No app-usage data was available.")
    lines.append("")

    lines.append("## 7. Time Distribution")
    lines.append("")
    if s.by_weekday:
        lines.append("**By weekday (min)**")
        for name in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
            if name in s.by_weekday:
                lines.append(f"- {name}: {s.by_weekday[name]}")
        lines.append("")
    if s.by_hour_bucket:
        lines.append("**By time of day (min)**")
        for bucket in (
            "early-morning",
            "morning",
            "midday",
            "afternoon",
            "evening",
            "late-night",
        ):
            if bucket in s.by_hour_bucket:
                lines.append(f"- {bucket}: {s.by_hour_bucket[bucket]}")
        lines.append("")
    if s.by_day:
        lines.append("**By date (min)**")
        for day_str in sorted(s.by_day.keys()):
            lines.append(f"- {day_str}: {s.by_day[day_str]}")
        lines.append("")

    if recap.regularities:
        lines.append("## 8. Long-Term Patterns Observed")
        lines.append("")
        for r in recap.regularities:
            lines.append(f"- {r}")
        lines.append("")

    if recap.change_vs_previous:
        lines.append("## 9. Compared With Previous Window")
        lines.append("")
        for line in _render_change_table(recap.change_vs_previous):
            lines.append(line)
        lines.append("")

    if recap.open_threads:
        lines.append("## 10. Open Threads (Last Known State)")
        lines.append("")
        for ot in recap.open_threads:
            lines.extend(_render_open_thread_markdown(ot))
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State persistence (used by --save-state / --previous-state for cross-window
# comparison without re-running the previous window's full pipeline)
# ---------------------------------------------------------------------------

def _recap_to_state(recap: Recap) -> dict:
    return {
        "since": recap.since.isoformat(),
        "until": recap.until.isoformat(),
        "window_label": recap.window_label,
        "headline": recap.headline,
        "generated_at": recap.generated_at.isoformat(),
        "stats": {
            "total_minutes": recap.stats.total_minutes,
            "sub_task_count": recap.stats.sub_task_count,
            "by_app": recap.stats.by_app,
            "by_weekday": recap.stats.by_weekday,
            "by_hour_bucket": recap.stats.by_hour_bucket,
            "by_day": recap.stats.by_day,
        },
        "themes": [
            {
                "name": t.name,
                "description": t.description,
                "apps": t.apps,
                "approx_minutes": t.approx_minutes,
                "evidence_ranges": t.evidence_ranges,
            }
            for t in recap.themes
        ],
    }


def _state_to_recap(payload: dict) -> Recap:
    s = payload.get("stats", {})
    stats = ActivityStats(
        since=date.fromisoformat(payload["since"]),
        until=date.fromisoformat(payload["until"]),
        total_minutes=int(s.get("total_minutes", 0) or 0),
        sub_task_count=int(s.get("sub_task_count", 0) or 0),
        by_app=dict(s.get("by_app", {})),
        by_weekday=dict(s.get("by_weekday", {})),
        by_hour_bucket=dict(s.get("by_hour_bucket", {})),
        by_day=dict(s.get("by_day", {})),
    )
    recap = Recap(
        since=stats.since,
        until=stats.until,
        window_label=str(payload.get("window_label", "previous")),
        generated_at=datetime.fromisoformat(
            payload.get("generated_at", datetime.now().isoformat())
        ),
        stats=stats,
        headline=str(payload.get("headline", "")),
    )
    for t in payload.get("themes", []):
        recap.themes.append(
            Theme(
                name=str(t.get("name", "")),
                description=str(t.get("description", "")),
                apps=list(t.get("apps", [])),
                approx_minutes=int(t.get("approx_minutes", 0) or 0),
                evidence_ranges=list(t.get("evidence_ranges", [])),
            )
        )
    return recap


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def _run(
    since: date,
    until: date,
    window_label: str,
    output: Path,
    mcp_url: str | None,
    model: str | None,
    compare_previous: bool,
    previous_state_path: Path | None,
    save_state_path: Path | None,
    owner_label: str,
) -> Path:
    llm_config = LLMConfig()
    if model:
        llm_config.model = model

    previous_state: Recap | None = None
    if previous_state_path is not None and previous_state_path.exists():
        try:
            previous_state = _state_to_recap(
                json.loads(previous_state_path.read_text(encoding="utf-8"))
            )
        except Exception as exc:
            logger.warning(
                "Failed to load previous-state file %s (%s); ignoring.",
                previous_state_path,
                exc,
            )

    client = OCMCPClient(url=mcp_url) if mcp_url else OCMCPClient()
    async with client as mcp:
        recap = await build_recap(
            mcp=mcp,
            llm=LLMClient(llm_config),
            since=since,
            until=until,
            window_label=window_label,
            compare_previous=compare_previous,
            previous_state=previous_state,
        )

    md = render_markdown(recap, owner_label=owner_label)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(md, encoding="utf-8")

    if save_state_path is not None:
        save_state_path.parent.mkdir(parents=True, exist_ok=True)
        save_state_path.write_text(
            json.dumps(_recap_to_state(recap), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return output


@app.command()
def main(
    week: str = typer.Option(None, "--week", help="ISO week, e.g. 2026-W19."),
    month: str = typer.Option(None, "--month", help="Calendar month, e.g. 2026-05."),
    since: str = typer.Option(None, "--since", help="Start date, YYYY-MM-DD."),
    until: str = typer.Option(None, "--until", help="End date, YYYY-MM-DD."),
    output: Path = typer.Option(None, "--output", "-o", help="Output Markdown path."),
    mcp_url: str = typer.Option(None, "--mcp-url", help="Override OpenChronicle MCP URL."),
    model: str = typer.Option(None, "--model", help="Override LLM model (e.g. qwen2.5:14b)."),
    compare_previous: bool = typer.Option(
        True,
        "--compare-previous/--no-compare-previous",
        help="Pull the immediately-preceding window for change_vs_previous.",
    ),
    previous_state: Path = typer.Option(
        None,
        "--previous-state",
        help="Reuse a previously-saved JSON state instead of recomputing the previous window.",
    ),
    save_state: Path = typer.Option(
        None,
        "--save-state",
        help="Write this run's state JSON for future --previous-state reuse.",
    ),
    owner_label: str = typer.Option("Me", "--owner", help="Owner label shown in the Markdown header."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose logging."),
) -> None:
    """Generate a long-term desktop-activity recap from OpenChronicle's local memory."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    s, u, label = _resolve_window(week, month, since, until)
    if output is None:
        if week:
            suffix = f"week-{week}"
        elif month:
            suffix = f"month-{month}"
        else:
            suffix = f"range-{s.isoformat()}_to_{u.isoformat()}"
        output = Path("recaps") / f"activity-{suffix}.md"

    try:
        result_path = asyncio.run(
            _run(
                since=s,
                until=u,
                window_label=label,
                output=output,
                mcp_url=mcp_url,
                model=model,
                compare_previous=compare_previous,
                previous_state_path=previous_state,
                save_state_path=save_state,
                owner_label=owner_label,
            )
        )
    except KeyboardInterrupt:
        sys.exit(130)

    typer.echo(f"Recap written to {result_path}")


if __name__ == "__main__":
    app()
