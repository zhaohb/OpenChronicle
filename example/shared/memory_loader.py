"""High-level helpers built on top of :class:`OCMCPClient`.

The MCP tools speak in dicts; this module promotes the shapes the example apps care about
into small dataclasses, and provides time-window iterators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import AsyncIterator, Iterable

from dateutil import parser as dateparser

from .mcp_client import OCMCPClient


@dataclass(slots=True)
class Entry:
    id: str
    timestamp: datetime
    tags: list[str]
    body: str
    path: str
    superseded_by: str | None = None

    @property
    def session_id(self) -> str | None:
        for tag in self.tags:
            if tag.startswith("sid:"):
                return tag[4:]
        return None

    @property
    def is_flush(self) -> bool:
        return "flush" in self.tags


@dataclass(slots=True)
class MemoryFile:
    path: str
    description: str
    tags: list[str]
    status: str
    entry_count: int
    entries: list[Entry] = field(default_factory=list)
    updated: datetime | None = None
    created: datetime | None = None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return dateparser.isoparse(value)
    except (ValueError, TypeError):
        return None


def _entry_from_dict(raw: dict, path: str) -> Entry:
    # `read_memory` returns the entry body under "body"; `recent_activity` and `search`
    # return the same payload from the FTS5 `entries` virtual table, where the column is
    # named "content". Accept either to keep callers oblivious.
    body = raw.get("body")
    if body is None:
        body = raw.get("content", "")

    tags_raw = raw.get("tags", [])
    if isinstance(tags_raw, str):
        # FTS rows store tags as a space-joined string; promote back to a list.
        tags = [t for t in tags_raw.split() if t]
    else:
        tags = list(tags_raw)

    return Entry(
        id=raw.get("id", ""),
        timestamp=_parse_dt(raw.get("timestamp")) or datetime.min,
        tags=tags,
        body=body,
        path=path,
        superseded_by=raw.get("superseded_by"),
    )


async def load_memory_files(
    client: OCMCPClient,
    prefixes: Iterable[str] | None = None,
    include_dormant: bool = False,
) -> list[MemoryFile]:
    """Return one :class:`MemoryFile` per matching memory file (without entries loaded)."""
    listing = await client.list_memories(include_dormant=include_dormant)
    files: list[MemoryFile] = []
    prefix_tuple = tuple(prefixes) if prefixes else None
    for raw in listing.get("files", []):
        path = raw.get("path", "")
        if prefix_tuple is not None and not any(path.startswith(p) for p in prefix_tuple):
            continue
        files.append(
            MemoryFile(
                path=path,
                description=raw.get("description", ""),
                tags=list(raw.get("tags", [])),
                status=raw.get("status", ""),
                entry_count=int(raw.get("entry_count", 0)),
                created=_parse_dt(raw.get("created")),
                updated=_parse_dt(raw.get("updated")),
            )
        )
    return files


async def load_file_with_entries(
    client: OCMCPClient,
    path: str,
    since: datetime | None = None,
    until: datetime | None = None,
    tail_n: int | None = None,
) -> MemoryFile:
    """Read one memory file with all (filtered) entries."""
    raw = await client.read_memory(
        path=path,
        since=since.isoformat() if since else None,
        until=until.isoformat() if until else None,
        tail_n=tail_n,
    )
    file = MemoryFile(
        path=raw.get("path", path),
        description=raw.get("description", ""),
        tags=list(raw.get("tags", [])),
        status=raw.get("status", ""),
        entry_count=int(raw.get("entry_count", 0)),
        updated=_parse_dt(raw.get("updated")),
    )
    for entry in raw.get("entries", []):
        file.entries.append(_entry_from_dict(entry, path))
    file.entries.sort(key=lambda e: e.timestamp)
    return file


async def load_recent_activity(
    client: OCMCPClient,
    since: datetime | None = None,
    limit: int = 50,
    prefix_filter: list[str] | None = None,
) -> list[Entry]:
    raw = await client.recent_activity(
        since=since.isoformat() if since else None,
        limit=limit,
        prefix_filter=prefix_filter,
    )
    entries: list[Entry] = []
    for item in raw.get("entries", []):
        entries.append(_entry_from_dict(item, item.get("path", "")))
    return entries


_EVENT_PATH_RE = re.compile(r"event-(\d{4}-\d{2}-\d{2})\.md$")


def _date_range(start: date, end: date) -> Iterable[date]:
    cur = start
    while cur <= end:
        yield cur
        cur = cur + timedelta(days=1)


async def iter_event_entries(
    client: OCMCPClient,
    since: date,
    until: date,
) -> AsyncIterator[Entry]:
    """Yield every event-daily entry in [since, until] (inclusive of both ends)."""
    for day in _date_range(since, until):
        path = f"event-{day.isoformat()}.md"
        try:
            file = await load_file_with_entries(client, path)
        except Exception:
            # Most days won't have a file — that's fine.
            continue
        for entry in file.entries:
            yield entry
