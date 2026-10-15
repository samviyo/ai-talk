"""Werewolf game server: MCP surface, LLM proxy, spectator, table runner, finale.

The frozen clients (werewolf/housebots, starter/bot) unpack
streamable_http_client() as a 3-tuple like mcp 1.x and read camelCase
result.structuredContent; the workspace pins mcp 2.3, which yields a 2-tuple
and snake_case structured_content. Server-owned launch paths (ww table, ww
final fallbacks, tests) call patch_mcp_client_compat() so that frozen code
runs unchanged under the pinned SDK.
"""

from __future__ import annotations


def patch_mcp_client_compat() -> None:
    try:
        from mcp.types import CallToolResult
    except ImportError:
        return
    if not hasattr(CallToolResult, "structuredContent"):
        CallToolResult.structuredContent = property(  # type: ignore[attr-defined]
            lambda self: self.structured_content
        )
    try:
        import contextlib

        import mcp.client.streamable_http as sh
    except ImportError:
        return
    original = sh.streamable_http_client
    if getattr(original, "_ww_compat", False):
        return

    @contextlib.asynccontextmanager
    async def streamable_http_client(url, **kwargs):
        async with original(url, **kwargs) as streams:
            if len(streams) == 2:
                yield streams[0], streams[1], None
            else:
                yield streams

    streamable_http_client._ww_compat = True  # type: ignore[attr-defined]
    sh.streamable_http_client = streamable_http_client
