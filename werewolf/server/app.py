"""Starlette app: MCP at /mcp, LLM proxy at /llm, spectator at / and /events."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.types import Receive, Scope, Send

from .mcp_tools import build_mcp_server
from .spectator import PAGE
from .state import AppState


def _bearer(request: Request) -> str | None:
    raw = request.headers.get("authorization", "")
    return raw[7:] if raw.lower().startswith("bearer ") else None


def _op_ok(request: Request, state: AppState) -> bool:
    return request.query_params.get("op") == state.operator_token


def create_app(state: AppState) -> Starlette:
    mcp_server = build_mcp_server(state)
    manager = StreamableHTTPSessionManager(app=mcp_server, json_response=True)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with manager.run():
            tasks = [asyncio.create_task(_ticker(t)) for t in state.tables]
            try:
                yield
            finally:
                for t in tasks:
                    t.cancel()

    async def index(request: Request) -> Response:
        if not _op_ok(request, state):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return HTMLResponse(PAGE)

    async def events(request: Request) -> Response:
        if not _op_ok(request, state):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        table = state.tables[0]
        queue: asyncio.Queue = asyncio.Queue()

        async def stream():
            try:
                async with table.lock:
                    table._notify_sse_locked()  # flush so history and queue never overlap
                    table.sse_subs.append(queue)
                    init, tick = table.spectator_init(), table.spectator_tick()
                    history = [{"kind": "event", **e.to_dict()} for e in table.game.events(None)]
                yield "data: " + json.dumps(init) + "\n\n"
                for m in history:
                    yield "data: " + json.dumps(m) + "\n\n"
                yield "data: " + json.dumps(tick) + "\n\n"
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=5.0)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield "data: " + json.dumps(msg) + "\n\n"
            finally:
                with contextlib.suppress(ValueError):
                    table.sse_subs.remove(queue)

        from starlette.responses import StreamingResponse

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    async def llm(request: Request) -> Response:
        auth = state.lookup(_bearer(request))
        if auth is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        table, seat = auth
        st = table.seats[seat]
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"error": {"code": "bad_schema", "message": "invalid json"}},
                status_code=400,
            )
        async with table.lock:
            await table.check_clock_locked()
            broke = table.is_broke(seat)
            if broke:
                return JSONResponse(
                    {"error": {"code": "broke", "message": "budget exhausted"}},
                    status_code=402,
                )
            if st.llm_busy:
                return JSONResponse(
                    {"error": {"code": "concurrent_call", "message": "one call in flight"}},
                    status_code=409,
                )
            st.llm_busy = True
            legal = table.game.legal_targets(seat)
            key = state.key_for(table, seat)
        try:
            status, body, charges = await state.proxy.chat_completions(
                payload, key or "", legal
            )
        finally:
            async with table.lock:
                st.llm_busy = False
        usd = charges.get("usd", 0.0)
        if usd:
            async with table.cond:
                table.charge_locked(seat, usd)
                table._notify_sse_locked()
                table._trace(seat, "llm", {"model": payload.get("model")}, str(status), 0.0)
        return JSONResponse(body, status_code=status)

    async def _ticker(table):
        from .table import ticker

        await ticker(table)

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/events", events),
            Route("/llm/v1/chat/completions", llm, methods=["POST"]),
            Mount("/mcp", app=manager.handle_request),
        ],
        lifespan=lifespan,
    )
    app.state.app_state = state
    return app
