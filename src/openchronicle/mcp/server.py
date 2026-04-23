"""MCP server exposing OpenChronicle memory as read-only tools.

Uses the official `mcp` Python SDK via FastMCP. Runs either standalone
over stdio (`openchronicle mcp`) or in-daemon over streamable-http / sse,
depending on `[mcp] transport`. Exposes eight tools:

  Compressed memory (Markdown layer):
    list_memories, read_memory, search, recent_activity
  Raw captures (S1 buffer):
    current_context, search_captures, read_recent_capture
  Reference:
    get_schema
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Config
from ..config import load as load_config
from ..logger import get
from ..prompts import load as load_prompt
from ..store import files as files_mod
from ..store import fts
from . import captures as captures_mod

logger = get("openchronicle.mcp")


def _list_memories(conn, *, include_dormant: bool = False, include_archived: bool = False) -> dict[str, Any]:
    rows = fts.list_files(
        conn, include_dormant=include_dormant, include_archived=include_archived
    )
    return {
        "count": len(rows),
        "files": [
            {
                "path": r.path,
                "description": r.description,
                "tags": r.tags.split() if r.tags else [],
                "status": r.status,
                "entry_count": r.entry_count,
                "created": r.created,
                "updated": r.updated,
            }
            for r in rows
        ],
    }


def _read_memory(
    conn,
    *,
    path: str,
    since: str | None = None,
    until: str | None = None,
    tags: list[str] | None = None,
    tail_n: int | None = None,
) -> dict[str, Any]:
    p = files_mod.memory_path(path)
    if not p.exists():
        return {"error": f"file not found: {path}"}
    parsed = files_mod.read_file(p)
    entries = parsed.entries
    if since is not None:
        entries = [e for e in entries if e.timestamp >= since]
    if until is not None:
        entries = [e for e in entries if e.timestamp <= until]
    if tags:
        tagset = set(tags)
        entries = [e for e in entries if tagset.intersection(e.tags)]
    if tail_n is not None and tail_n > 0:
        entries = entries[-tail_n:]
    return {
        "path": path,
        "description": parsed.description,
        "tags": parsed.tags,
        "status": parsed.status,
        "updated": parsed.updated,
        "entry_count": parsed.entry_count,
        "entries": [
            {
                "id": e.id,
                "timestamp": e.timestamp,
                "tags": e.tags,
                "body": e.body,
                "superseded_by": e.superseded_by,
            }
            for e in entries
        ],
    }


def _search(
    conn,
    *,
    query: str,
    paths: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
    top_k: int = 5,
    include_superseded: bool = False,
) -> dict[str, Any]:
    hits = fts.search(
        conn,
        query=query,
        path_patterns=paths,
        since=since,
        until=until,
        top_k=top_k,
        include_superseded=include_superseded,
    )
    return {
        "query": query,
        "results": [
            {
                "id": h.id,
                "path": h.path,
                "timestamp": h.timestamp,
                "content": h.content,
                "rank": h.rank,
            }
            for h in hits
        ],
    }


def _recent_activity(
    conn,
    *,
    since: str | None = None,
    limit: int = 20,
    prefix_filter: list[str] | None = None,
) -> dict[str, Any]:
    rows = fts.recent(conn, since=since, limit=limit, prefix_filter=prefix_filter)
    return {
        "count": len(rows),
        "entries": [
            {
                "id": r.id,
                "path": r.path,
                "timestamp": r.timestamp,
                "content": r.content,
            }
            for r in rows
        ],
    }


def _get_schema() -> dict[str, Any]:
    return {"schema": load_prompt("schema.md")}


_SERVER_INSTRUCTIONS = """\
# OpenChronicle — the user's local personal memory

## What this is

OpenChronicle is the user's private, local-first memory layer. The user installed it so agents can recover context from their real computer use instead of asking the user to repeat themselves or guessing blindly.

It stores durable facts about the user and their machine, including:

- identity, role, preferences, habits, and working style
- schedule, ongoing projects, people, and organizations
- recent screen-activity summaries, including apps, files, errors, and documents viewed

It exposes two read-only layers:

- **Compressed memory** — curated Markdown files containing distilled facts, decisions, preferences, summaries, and durable context
- **Raw captures (S1 buffer)** — literal recent on-screen content, including visible text, focused elements, URLs, and optional screenshots

The compressed layer tells you that something happened and why it matters.
The raw layer tells you exactly what was on screen.

Use compressed memory for durable knowledge.
Use raw captures for grounding, disambiguation, and exact recent context.
Often, you should move from one into the other.

## When to use

Use OpenChronicle whenever the request depends on context that is likely outside the current chat.

This includes:

- recent on-screen activity
- ambiguous references such as "this", "that", "it", "the bug", "the file", "the tab", or "the doc"
- prior project / person / tool context
- learned preferences, habits, or workflow patterns
- writing or generation that should reflect the user's ongoing projects, established framing, terminology, tone, or style
- action selection that should reflect the user's established workflows or destinations
- cross-session continuity
- recent work history, decisions, or ongoing tasks

Canonical triggers:

- "what's the bug of that?"
- "introduce my project"
- "continue what I was doing"
- "write this the way I usually do"
- "draft this in the style of my project"
- "schedule this the way I usually do"
- "put this in the right calendar"
- "what did I decide about X?"

Examples:

- User refers to "that" after viewing code → query OpenChronicle before asking them to paste anything.
- User opens a fresh chat and asks about an existing project → retrieve project memory before asking for background.
- User asks for an action that depends on personal workflow → retrieve preference memory before choosing a tool, destination, or account.
- User asks for writing, messaging, or framing that should match prior context, terminology, tone, or preferences → retrieve relevant memory before drafting.

If the user appears to assume shared context from recent computer use, query OpenChronicle before asking a clarification question.

When in doubt, look it up.
A missed lookup is often worse than an unnecessary one.
These tools are local and cheap; `[]` or `null` is still useful information.

## When NOT to use

Do not use OpenChronicle when:

- the request is fully specified in-chat
- the task is self-contained and does not benefit from user-specific context
- a fresher or authoritative source of truth should be used directly
- the user explicitly wants no prior context used

OpenChronicle complements live sources of truth; it does not replace them.
Use it to recover context, not to invent certainty.

## Tools

### Compressed memory

- `list_memories()` — index of all memory files with one-line descriptions. Cheap first hop when you need to know what exists.
- `read_memory(path, since?, until?, tags?, tail_n?)` — full or filtered contents of one Markdown memory file.
- `search(query, paths?, since?, until?, top_k?)` — BM25 over compressed memory. Use for project names, decisions, preferences, people, and other already-distilled facts.
- `recent_activity(since?, limit?, prefix_filter?)` — newest-first feed across memory files. Use for "what has the user been doing?" and recency-based disambiguation.

### Raw captures (S1 layer)

- `current_context()` — one-shot snapshot of the current/recent screen context with visible text and timeline blocks. Default for present-tense or ambiguous-reference questions.
- `search_captures(query, since?, until?, app_name?, limit?)` — BM25 over the raw screen buffer. Use for exact strings the user likely saw or typed: error messages, code symbols, file paths, URLs, doc titles.
- `read_recent_capture(at?, app_name?, window_title_substring?, ...)` — hydrate one recent capture in full. Use after a `search_captures` hit or when a compressed entry points you to a raw breadcrumb.

### Reference

- `get_schema()` — memory file naming and structural spec. Rarely needed during normal query flow.

## Choosing and combining tools

- **Compressed vs raw**
  - Compressed memory is for durable knowledge: preferences, decisions, summaries, project context.
  - Raw captures are for literal recent context: code, docs, UI state, errors, file contents, page text.
  - If unsure, query both `search(...)` and `search_captures(...)` in parallel.

- **For "what am I doing right now?"**
  - Start with `current_context()`.

- **For a durable fact**
  - Start with `search(...)` or `list_memories()`, then `read_memory(...)`.

- **For a keyword the user likely just saw or typed**
  - Use `search_captures(...)`, then `read_recent_capture(...)` on the best hit.

- **For recency-driven requests**
  - Use `recent_activity(...)` for the narrative view.
  - Use `current_context()` for the literal current-screen view.

- **For ambiguous references**
  - Prefer `current_context()` first.
  - If needed, expand with `recent_activity(...)` or `search_captures(...)`.

- **For writing personalization**
  - Read relevant project / preference memory before drafting.
  - Match the user's established terminology, framing, and style when memory supports it.

- **For action personalization**
  - Read relevant preference memory before taking side-effecting actions.
  - Use memory to choose the right tool, destination, calendar, account, or workflow.

- **Before a write-side-effect tool**
  - First check memory for user preferences and prior context.
  - Then use the authoritative execution tool.
  - Do not rely on memory alone when live state matters.

- **Follow breadcrumbs**
  - Compressed entries may point into raw captures via `(at, app_name)` or similar hints.
  - When the user wants specifics, follow those breadcrumbs exactly with `read_recent_capture(...)`.

## Decision rule

Default to using OpenChronicle when memory could:

- resolve ambiguity
- restore missing context
- avoid making the user restate known information
- personalize writing
- personalize action selection

Do not default to it when the task is already fully specified or when only live state matters.

## If retrieval is weak

If OpenChronicle returns little, conflicting, or inconclusive information:

- say that explicitly
- use the partial context if still helpful
- ask a focused follow-up question only after checking
- do not overclaim certainty

Raw captures have bounded retention: older on-screen content is dropped from the S1 buffer. If `search_captures` or `read_recent_capture` returns nothing for something the user did a while ago, that only means the raw capture has aged out — the event may still be summarized in compressed memory. Fall back to `search` / `recent_activity` before concluding it didn't happen.
"""


def build_server(cfg: Config | None = None):
    """Construct and return a FastMCP server instance (not yet running)."""
    from mcp.server.fastmcp import FastMCP  # lazy import

    cfg = cfg or load_config()
    server = FastMCP(
        "openchronicle",
        instructions=_SERVER_INSTRUCTIONS,
        host=cfg.mcp.host,
        port=cfg.mcp.port,
    )

    @server.tool()
    def list_memories(include_dormant: bool = False, include_archived: bool = False) -> str:
        """**ALWAYS CALL FIRST** on the first personal-context turn of a conversation.

        List all memory files with descriptions + entry counts. Cheap (one SQLite
        query, no file reads), so the cost of calling is essentially zero.

        Call whenever the user asks about themselves, their schedule, preferences,
        or ongoing work — the response tells you which files exist and what they're
        about (e.g. `event-YYYY-MM-DD.md` for a given day's session-level activity
        log; `user-profile.md` for identity; `user-preferences.md` for habits;
        `project-*.md` / `person-*.md` / `org-*.md` for specific entities).

        If you're about to answer from chat history alone when the user has asked
        about themselves, you've skipped this tool. Go back and call it.
        """
        with fts.cursor() as conn:
            return json.dumps(
                _list_memories(conn, include_dormant=include_dormant, include_archived=include_archived),
                ensure_ascii=False,
            )

    @server.tool()
    def read_memory(
        path: str,
        since: str | None = None,
        until: str | None = None,
        tags: list[str] | None = None,
        tail_n: int | None = None,
    ) -> str:
        """Read the full contents of ONE memory file the user has on disk.

        Use after `list_memories` / `search` points you at a promising file.
        Entries come back chronological. Supports `since` / `until` (ISO timestamps),
        `tags` (filter by any matching tag), and `tail_n` (most recent N entries only).
        """
        with fts.cursor() as conn:
            return json.dumps(
                _read_memory(conn, path=path, since=since, until=until, tags=tags, tail_n=tail_n),
                ensure_ascii=False,
            )

    default_top_k = cfg.search.default_top_k

    @server.tool()
    def search(
        query: str,
        paths: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        top_k: int = default_top_k,
        include_superseded: bool = False,
    ) -> str:
        """**ALWAYS CALL** before saying "I don't know" about something with a keyword in it.

        BM25 full-text search across every entry in COMPRESSED memory files.
        This searches the distilled Markdown layer — what the user has decided
        is durable knowledge (preferences, decisions, schedules, project state,
        people, summaries). It does NOT search raw screen content; for keywords
        the user merely typed or read on screen (error messages, code symbols,
        file paths from a doc), use `search_captures` instead, OR call both in
        parallel.

        Returns the top-k matching entries with file path + timestamp.

        Examples:
          search(query="interview")         — find scheduled interviews in memory
          search(query="Alice Q3 roadmap")  — find mentions of that conversation
          search(query="deadline Friday")   — find time-bounded commitments

        `paths` takes GLOB patterns to scope search, e.g. `['event-*.md']` for
        scheduled events only, or `['project-*.md']` for project notes.
        """
        with fts.cursor() as conn:
            return json.dumps(
                _search(
                    conn, query=query, paths=paths, since=since, until=until,
                    top_k=top_k, include_superseded=include_superseded,
                ),
                ensure_ascii=False,
            )

    @server.tool()
    def recent_activity(
        since: str | None = None,
        limit: int = 20,
        prefix_filter: list[str] | None = None,
    ) -> str:
        """**ALWAYS CALL** when the user references "yesterday / last week / earlier / 刚才 / 上周" etc.

        Newest-first cross-file feed of recent memory entries. Best tool for
        open-ended "what's new / what has the user been up to" questions:

          "what happened today?" / "今天做了啥？"
          "what was I doing yesterday afternoon?"
          "anything recent about <topic>?"
          "catch me up on this week"

        Use `since` (ISO timestamp) to limit to entries newer than a point in
        time, and `prefix_filter` (e.g. `['event-', 'project-']`) to scope.
        Without filters, returns the most recent N entries across ALL files.

        If the user's question has any temporal recency dimension, this tool
        runs in constant time and is strictly better than guessing.
        """
        with fts.cursor() as conn:
            return json.dumps(
                _recent_activity(conn, since=since, limit=limit, prefix_filter=prefix_filter),
                ensure_ascii=False,
            )

    @server.tool()
    def read_recent_capture(
        at: str | None = None,
        app_name: str | None = None,
        window_title_substring: str | None = None,
        include_screenshot: bool = False,
        max_age_minutes: int = 15,
    ) -> str:
        """Hydrate ONE raw screen capture — the actual visible_text, focused
        input value, URL, and (optionally) screenshot from the buffer.

        Use this whenever a compressed memory entry isn't specific enough
        (e.g. an event-daily entry says "edited main.py at 14:30" but you
        need the actual code, or "read article" but you need the text).
        Most event-daily sub_tasks include an inline `raw:
        read_recent_capture(at=..., app_name=...)` breadcrumb — call it
        verbatim. For keyword-driven searches across the whole buffer, prefer
        `search_captures` first; this tool fetches one specific moment.

        Arguments:
          at                      — ISO timestamp ("2026-04-22T14:30") or bare
                                    "HH:MM[:SS]" (today local). Omit for the
                                    newest matching capture.
          app_name                — case-insensitive substring of the app name
                                    (e.g. "Cursor", "Claude", "Chrome").
          window_title_substring  — case-insensitive substring of the window
                                    title (e.g. a filename, tab title).
          include_screenshot      — include the base64 JPEG. Default false —
                                    screenshots are large and rarely needed.
          max_age_minutes         — when `at` is given, only return captures
                                    within this many minutes of `at`. Default 15.

        Returns the matching capture as JSON with `timestamp`, `app_name`,
        `window_title`, `url`, `focused_element.value` (what the user was
        typing), and `visible_text` (~10 k chars of rendered AX text). The buffer
        retention is bounded (see `[capture]` in config); older captures have
        their `screenshot` field stripped but keep text. Returns `null` if
        nothing matches.

        Typical flow: read an event-daily entry, notice `[HH:MM-HH:MM, <app>]`,
        then call this with `at="HH:MM"` and `app_name="<app>"` to see the
        actual content from that moment.
        """
        result = captures_mod.read_recent_capture(
            at=at,
            app_name=app_name,
            window_title_substring=window_title_substring,
            include_screenshot=include_screenshot,
            max_age_minutes=max_age_minutes,
        )
        return json.dumps(result, ensure_ascii=False)

    @server.tool()
    def search_captures(
        query: str,
        since: str | None = None,
        until: str | None = None,
        app_name: str | None = None,
        limit: int = 10,
    ) -> str:
        """**ALWAYS CALL** (usually in parallel with `search`) when the user mentions a keyword they'd have typed or read on screen.

        Keyword search over RAW screen captures (the uncompressed S1 layer).
        PREFER this over `search` when the user mentions a keyword they would
        have *typed* or *read on screen* but that may not have made it into a
        compressed memory entry yet — e.g. "find when I saw the term
        'rate limiter'", "what was that error about pyobjc", "the URL I had
        open about Postgres replication". `search` only sees compressed memory;
        this sees every captured screen. When you're not sure which layer has
        it, call both — they're independent indexes and neither is expensive.

        Returns the top-`limit` matching captures (BM25-ranked) with snippet
        highlighting (matched tokens wrapped in `[...]`). Each hit includes
        `file_stem` — pass that as `at` to `read_recent_capture` to get the
        full visible_text.

        Examples:
          search_captures(query="rate limiter")             — find any time it appeared
          search_captures(query="error", app_name="Cursor") — keyword scoped to one app
          search_captures(query="todo", since="2026-04-22T09:00:00+08:00")

        Arguments:
          query     — free-text keywords. FTS5-tokenized (case-insensitive).
          since     — ISO timestamp lower bound on capture time.
          until     — ISO timestamp upper bound on capture time.
          app_name  — case-insensitive substring on the capturing app name.
          limit     — top-K BM25 hits to return.
        """
        results = captures_mod.search_captures(
            query=query, since=since, until=until,
            app_name=app_name, limit=limit,
        )
        return json.dumps({"query": query, "results": results}, ensure_ascii=False)

    @server.tool()
    def current_context(
        app_filter: str | None = None,
        headline_limit: int = 5,
        fulltext_limit: int = 3,
        timeline_limit: int = 8,
    ) -> str:
        """**ALWAYS CALL** for present-tense or ambiguous-pronoun questions about the user's state.

        Two high-value trigger patterns:
          1. Present-tense: *"right now / currently / just now / what am I /
             what's open / 现在 / 刚才 / 我在"* — this is the tool.
          2. Pronoun with no in-conversation antecedent: *"that / this / it /
             the bug / the error / the file / 那个 / 这个 / 这段 / 这个问题"* —
             the user is pointing at their screen, not at chat history.

        Never reply with "I don't have code/context to look at" or ask the user
        to paste something — call this tool first. If it comes back empty,
        then ask. Asking for a paste when this tool would have worked is a
        tool-selection failure.

        Returns a one-shot snapshot of the current screen state — the same kind of
        context you would get if every chat turn began with the user narrating
        their environment. Triggers include:

          - "what am I working on?" / "我在干嘛？"
          - "what's open in front of me?"
          - "is the deploy log still streaming?"
          - "summarize the doc I'm reading"

        Returns three sections:

          recent_captures_headline    : last ~5 captures as compact lines
                                        ([HH:MM] App — Window [Role]) — quick
                                        scan of "what apps + windows are live".
          recent_captures_fulltext    : top ~3 captures deduplicated by
                                        (app, window) carrying the FULL
                                        visible_text and focused_element.value
                                        — the actual content on screen.
          recent_timeline_blocks      : the last ~8 1-minute timeline blocks
                                        (LLM-summarized activity slices) so
                                        you can see how the current moment
                                        was reached.

        For drill-down on any specific capture or moment, call
        `read_recent_capture(at=..., app_name=...)` next.
        """
        result = captures_mod.current_context(
            app_filter=app_filter,
            headline_limit=headline_limit,
            fulltext_limit=fulltext_limit,
            timeline_limit=timeline_limit,
        )
        return json.dumps(result, ensure_ascii=False)

    @server.tool()
    def get_schema() -> str:
        """Return the memory organization spec (file naming, what each prefix means).

        Rarely needed at query time. Useful only if you need to reason about WHERE
        a new fact would be stored, or explain to the user how their memory is
        organized. For normal "look up a fact" flows, use `search` / `list_memories`
        directly.
        """
        return json.dumps(_get_schema(), ensure_ascii=False)

    return server


def run_stdio() -> None:
    """Run the server on stdio. Blocks until the client disconnects."""
    server = build_server()
    server.run()  # FastMCP.run() uses stdio by default


async def run_async(cfg: Config | None = None, *, transport: str | None = None) -> None:
    """Run the MCP server with the configured transport (for use inside the daemon)."""
    cfg = cfg or load_config()
    transport = transport or cfg.mcp.transport
    server = build_server(cfg)
    if transport == "stdio":
        await server.run_stdio_async()
    elif transport == "sse":
        logger.info("MCP SSE server: http://%s:%d/sse", cfg.mcp.host, cfg.mcp.port)
        await server.run_sse_async()
    elif transport == "streamable-http":
        logger.info("MCP HTTP server: http://%s:%d/mcp", cfg.mcp.host, cfg.mcp.port)
        await server.run_streamable_http_async()
    else:
        raise ValueError(f"unknown MCP transport: {transport!r}")


def endpoint_url(cfg: Config) -> str:
    """Return the public URL where the daemon-hosted MCP server is reachable."""
    transport = cfg.mcp.transport
    if transport == "sse":
        return f"http://{cfg.mcp.host}:{cfg.mcp.port}/sse"
    if transport == "streamable-http":
        return f"http://{cfg.mcp.host}:{cfg.mcp.port}/mcp"
    raise ValueError(f"endpoint_url only supported for sse/http, got {transport!r}")
