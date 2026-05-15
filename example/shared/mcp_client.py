"""Async MCP client for the OpenChronicle daemon.

OpenChronicle hosts a streamable-http MCP server at ``http://127.0.0.1:8742/mcp`` by default.
Every tool returns a JSON string; this client parses it back into Python objects so the
example apps can stay focused on business logic.

Corporate ``HTTP_PROXY`` / ``HTTPS_PROXY`` often hijacks loopback and returns **403**
from the proxy (not from OpenChronicle). This client uses ``trust_env=False`` on the
httpx client — same rationale as ``LLMClient``.

The MCP SDK may validate ``Origin`` on localhost; we set ``Origin`` to the MCP URL
origin (scheme + host + port) so DNS-rebinding checks pass when the daemon enables them.

Example:

    async with OCMCPClient() as oc:
        files = await oc.list_memories()
        entries = await oc.recent_activity(limit=50)
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from typing import Any
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)

DEFAULT_MCP_URL = os.environ.get("OC_MCP_URL", "http://127.0.0.1:8742/mcp")


class MCPCallError(RuntimeError):
    """Raised when an MCP tool call returns an error or unparseable result."""

    def __init__(self, tool: str, detail: str):
        super().__init__(f"MCP tool {tool!r} failed: {detail}")
        self.tool = tool
        self.detail = detail


class OCMCPClient:
    """Thin async wrapper around the OpenChronicle MCP tool surface."""

    def __init__(self, url: str = DEFAULT_MCP_URL):
        self.url = url
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "OCMCPClient":
        self._stack = AsyncExitStack()
        try:
            parsed = urlparse(self.url)
            default_headers: dict[str, str] = {}
            if parsed.scheme and parsed.netloc:
                default_headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
            http_client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(
                    MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT
                ),
                headers=default_headers,
                trust_env=False,
            )
            await self._stack.enter_async_context(http_client)
            transport = await self._stack.enter_async_context(
                streamable_http_client(self.url, http_client=http_client)
            )
            # streamable-http returns (read_stream, write_stream, get_session_id)
            read_stream, write_stream, *_ = transport
            session = await self._stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()
            self._session = session
        except Exception:
            await self._stack.aclose()
            self._stack = None
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def _call(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("OCMCPClient used outside of `async with` block")

        result = await self._session.call_tool(tool, arguments or {})
        if result.isError:
            raise MCPCallError(tool=tool, detail=str(result.content))

        # OpenChronicle tools return a single text content with a JSON payload.
        text_chunks: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                text_chunks.append(text)
        joined = "\n".join(text_chunks).strip()
        if not joined:
            return None
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            # Fall back to raw text — get_schema returns markdown.
            return joined

    # ------------------------------------------------------------------
    # Memory layer
    # ------------------------------------------------------------------

    async def list_memories(
        self,
        include_dormant: bool = False,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        return await self._call(
            "list_memories",
            {"include_dormant": include_dormant, "include_archived": include_archived},
        )

    async def read_memory(
        self,
        path: str,
        since: str | None = None,
        until: str | None = None,
        tags: list[str] | None = None,
        tail_n: int | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path}
        if since is not None:
            args["since"] = since
        if until is not None:
            args["until"] = until
        if tags:
            args["tags"] = tags
        if tail_n is not None:
            args["tail_n"] = tail_n
        return await self._call("read_memory", args)

    async def search(
        self,
        query: str,
        paths: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        top_k: int = 5,
        include_superseded: bool = False,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "query": query,
            "top_k": top_k,
            "include_superseded": include_superseded,
        }
        if paths:
            args["paths"] = paths
        if since is not None:
            args["since"] = since
        if until is not None:
            args["until"] = until
        return await self._call("search", args)

    async def recent_activity(
        self,
        since: str | None = None,
        limit: int = 20,
        prefix_filter: list[str] | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"limit": limit}
        if since is not None:
            args["since"] = since
        if prefix_filter:
            args["prefix_filter"] = prefix_filter
        return await self._call("recent_activity", args)

    # ------------------------------------------------------------------
    # Raw capture layer
    # ------------------------------------------------------------------

    async def search_captures(
        self,
        query: str,
        since: str | None = None,
        until: str | None = None,
        app_name: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"query": query, "limit": limit}
        if since is not None:
            args["since"] = since
        if until is not None:
            args["until"] = until
        if app_name is not None:
            args["app_name"] = app_name
        return await self._call("search_captures", args)

    async def current_context(
        self,
        app_filter: str | None = None,
        headline_limit: int = 5,
        fulltext_limit: int = 3,
        timeline_limit: int = 8,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "headline_limit": headline_limit,
            "fulltext_limit": fulltext_limit,
            "timeline_limit": timeline_limit,
        }
        if app_filter is not None:
            args["app_filter"] = app_filter
        return await self._call("current_context", args)

    async def read_recent_capture(
        self,
        at: str | None = None,
        app_name: str | None = None,
        window_title_substring: str | None = None,
        include_screenshot: bool = False,
        max_age_minutes: int = 15,
    ) -> dict[str, Any] | None:
        args: dict[str, Any] = {
            "include_screenshot": include_screenshot,
            "max_age_minutes": max_age_minutes,
        }
        if at is not None:
            args["at"] = at
        if app_name is not None:
            args["app_name"] = app_name
        if window_title_substring is not None:
            args["window_title_substring"] = window_title_substring
        return await self._call("read_recent_capture", args)
