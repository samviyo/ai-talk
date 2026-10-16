"""Starlette app: MCP at /mcp, LLM proxy at /llm, spectator at / and /events."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
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

    async def admin_house(request: Request) -> Response:
        if not _op_ok(request, state):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "bad_schema"}, status_code=400)
        table = state.tables[0]
        async with table.cond:
            for k, brain in payload.items():
                seat = int(k)
                if 0 <= seat < len(table.seats):
                    table.seats[seat].house = str(brain)
            table._push_sse_locked(table.spectator_init())
            table._push_sse_locked(table.spectator_tick())
        return JSONResponse({"ok": True})

    async def admin_start(request: Request) -> Response:
        if not _op_ok(request, state):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        table = state.tables[0]
        async with table.cond:
            await table.check_clock_locked()
            if not table.awaiting_start:
                return JSONResponse({"error": "already_started"}, status_code=409)
            if len(table.ready_seats) < table.config.seats:
                missing = sorted(set(range(table.config.seats)) - table.ready_seats)
                return JSONResponse(
                    {"error": "not_ready", "waiting": missing}, status_code=409
                )
            if table.game.result is not None or not table._game_started:
                # the lobby still sits on a placeholder or a finished game
                table.start_game_locked()
            table.begin_locked()
            table._push_sse_locked(table.spectator_tick())
            table.cond.notify_all()
        return JSONResponse({"ok": True, "game": table.game_counter})

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
        async with table.cond:
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
            started_game = table.game_counter
        t0 = time.monotonic()
        try:
            status, body, charges = await state.proxy.chat_completions(
                payload, key or "", legal
            )
        finally:
            # Charge and clear busy under one lock so no call sneaks past broke.
            async with table.cond:
                st.llm_busy = False
                usd = charges.get("usd", 0.0)
                if usd:
                    if table.game_counter == started_game:
                        table.charge_locked(seat, usd)
                        table._notify_sse_locked()
                    else:
                        # the game that issued the call already settled; record only
                        table._trace(
                            seat, "llm_cross_game",
                            {"game": started_game, "usd": round(usd, 6)}, "ok", 0.0,
                        )
                table._trace(
                    seat, "llm", {"model": payload.get("model")}, str(status),
                    (time.monotonic() - t0) * 1000,
                )
        return JSONResponse(body, status_code=status)

    async def _ticker(table):
        from .table import ticker

        await ticker(table)

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/events", events),
            Route("/admin/house", admin_house, methods=["POST"]),
            Route("/admin/start", admin_start, methods=["POST"]),
            Route("/llm/v1/chat/completions", llm, methods=["POST"]),
            Mount("/mcp", app=manager.handle_request),
        ],
        lifespan=lifespan,
    )
    app.state.app_state = state
    return app
