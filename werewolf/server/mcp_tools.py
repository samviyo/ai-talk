"""MCP tool surface. Every game mutation and clock check under the table lock."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.context import ServerRequestContext
from mcp.types import CallToolResult, TextContent

from werewolf.engine import DoctorSave, IllegalAction, NightKill, Post, SeerCheck, Vote

from .state import AppState
from .table import TOOL_CALL_BUDGET, Table

WAIT_DEFAULT_S = 25.0
# Frozen clients use httpx's 5s default read timeout; long-poll must beat it.
WAIT_MAX_S = 3.5

S = {"type": "integer"}
TOOLS: list[tuple[str, dict[str, Any], list[str]]] = [
    (
        "wait_for_turn",
        {"timeout_s": {"type": "number"}},
        [],
    ),
    ("get_my_role", {}, []),
    ("read_board", {"since": S}, []),
    (
        "post",
        {
            "turn_id": {"type": "string"},
            "chat": {"type": "string"},
            "claimed_role": {"type": "string"},
            "accuse": S,
        },
        ["turn_id", "chat"],
    ),
    ("vote", {"turn_id": {"type": "string"}, "seat": S}, ["turn_id", "seat"]),
    ("night_action", {"turn_id": {"type": "string"}, "target": S}, ["turn_id", "target"]),
    ("wolf_chat", {"msg": {"type": "string"}}, ["msg"]),
    ("read_wolf_chat", {"since": S}, []),
    ("get_budget", {}, []),
]


def _ok(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=__import__("json").dumps(payload))],
        structured_content=payload,
    )


def _err(code: str, msg: str = "") -> CallToolResult:
    return _ok({"error": code, "message": msg or code})


def _auth(ctx: ServerRequestContext[Any, Any], state: AppState) -> tuple[Table, int] | None:
    request = ctx.request
    headers = getattr(request, "headers", None) or {}
    raw = headers.get("authorization", "")
    token = raw[7:] if raw.lower().startswith("bearer ") else None
    return state.lookup(token)


class Dispatcher:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.calls: dict[tuple[str, int], tuple[str, int]] = {}  # (table,seat) -> (turn_id, count)

    def _budget_check(
        self, table: Table, seat: int, tool: str
    ) -> CallToolResult | None:
        g = table.game
        key = (table.name, seat)
        if tool == "wait_for_turn":
            return None
        turn_id, count = self.calls.get(key, (g.turn_id, 0))
        if turn_id != g.turn_id:
            turn_id, count = g.turn_id, 0
        if count >= TOOL_CALL_BUDGET:
            return _err("call_budget_exceeded")
        self.calls[key] = (turn_id, count + 1)
        return None

    async def dispatch(
        self, ctx: ServerRequestContext[Any, Any], name: str, args: dict[str, Any]
    ) -> CallToolResult:
        t0 = time.monotonic()
        auth = _auth(ctx, self.state)
        if auth is None:
            return _err("unauthorized")
        table, seat = auth
        code = "ok"
        try:
            result = await self._run(table, seat, name, args)
            if isinstance(result, CallToolResult):
                payload = result.structured_content or {}
                if isinstance(payload, dict) and payload.get("error"):
                    code = str(payload["error"])
                return result
            return _ok(result)
        finally:
            latency = (time.monotonic() - t0) * 1000
            if name != "wait_for_turn":
                table._trace(seat, name, _safe_args(args), code, latency)

    async def _run(
        self, table: Table, seat: int, name: str, args: dict[str, Any]
    ) -> dict[str, Any] | CallToolResult:
        if name == "wait_for_turn":
            return await self._wait_for_turn(table, seat, args)
        async with table.cond:
            await table.check_clock_locked()
            if table.game.result is not None:
                table._notify_sse_locked()
                table.cond.notify_all()
            over = table.game.result is not None
            if name == "get_my_role":
                table.ready_seats.add(seat)
                if len(table.ready_seats) == table.config.seats:
                    table.ready_event.set()
                self._budget_check_reset(table, seat)
                return self._role(table, seat)
            if over and name in ("post", "vote", "night_action", "wolf_chat"):
                return _err("not_your_turn", "game over")
            if not over:
                budget_err = self._budget_check(table, seat, name)
                if budget_err is not None:
                    return budget_err
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return _err("bad_schema", f"unknown tool {name}")
            result = handler(table, seat, args)
            if table.game.result is not None and not table._run_written:
                table._run_written = True
                table.write_run()
            table._notify_sse_locked()
            table.cond.notify_all()
            return result

    # --- individual tools ----------------------------------------------------

    async def _wait_for_turn(
        self, table: Table, seat: int, args: dict[str, Any]
    ) -> dict[str, Any]:
        timeout = min(float(args.get("timeout_s") or WAIT_DEFAULT_S), WAIT_MAX_S)
        deadline = time.monotonic() + timeout
        async with table.cond:
            while True:
                await table.check_clock_locked()
                g = table.game
                table._notify_sse_locked()
                if g.result is not None:
                    self._budget_check_reset(table, seat)
                    return {"status": "over", "result": g.result}
                owe = g.pending().get(seat)
                # Serve each window once per seat; re-polls wait for the next.
                if owe is not None and table._served_turn.get(seat) != g.turn_id:
                    table._served_turn[seat] = g.turn_id
                    announcements = table.announcements_locked(seat)
                    return {
                        "turn_id": g.turn_id,
                        "phase": g.phase,
                        "day": g.day,
                        "round": g.round,
                        "you_owe": owe,
                        "legal_targets": g.legal_targets(seat),
                        "announcements": announcements,
                        "deadline_s": round(table.deadline() - time.monotonic(), 2),
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"status": "waiting"}
                try:
                    await asyncio.wait_for(table.cond.wait(), timeout=remaining)
                except TimeoutError:
                    return {"status": "waiting"}

    def _budget_check_reset(self, table: Table, seat: int) -> None:
        self.calls[(table.name, seat)] = (table.game.turn_id, 0)

    def _role(self, table: Table, seat: int) -> dict[str, Any]:
        g = table.game
        role = g.role(seat)
        partners: list[int] = []
        if role == "werewolf":
            partners = [
                s for s in range(g.config.seats) if s != seat and g.role(s) == "werewolf"
            ]
        return {"seat": seat, "role": role, "partners": partners}

    def _tool_get_my_role(self, table: Table, seat: int, args: dict) -> dict:
        return self._role(table, seat)

    def _tool_read_board(self, table: Table, seat: int, args: dict) -> dict:
        st = table.seats[seat]
        since = int(args.get("since") or 0)
        posts: list[dict[str, Any]] = []
        next_id = st.since["board"]
        for e in table.game.events(seat):
            if e.kind != "posts_revealed":
                continue
            for p in e.data["posts"]:
                if p["id"] >= since:
                    posts.append(p)
                next_id = max(next_id, p["id"] + 1)
        st.since["board"] = next_id
        return {"posts": posts, "next": next_id}

    def _tool_post(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        if table.is_broke(seat):
            return _err("broke", "seat budget exhausted")
        stale = self._check_turn_id(table, args)
        if stale is not None:
            return stale
        try:
            table.game.act(
                seat,
                Post(
                    chat=str(args.get("chat") or ""),
                    claimed_role=args.get("claimed_role"),
                    accuse=args.get("accuse"),
                ),
            )
        except IllegalAction as e:
            return _err(e.code, e.msg)
        return {"ok": True}

    def _tool_vote(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        stale = self._check_turn_id(table, args)
        if stale is not None:
            return stale
        try:
            table.game.act(seat, Vote(target=int(args.get("seat"))))
        except (IllegalAction, TypeError, ValueError) as e:
            code = e.code if isinstance(e, IllegalAction) else "bad_schema"
            return _err(code, str(e))
        return {"ok": True}

    def _tool_night_action(
        self, table: Table, seat: int, args: dict
    ) -> dict | CallToolResult:
        stale = self._check_turn_id(table, args)
        if stale is not None:
            return stale
        target = args.get("target")
        obligation = table.game.pending().get(seat)
        try:
            if obligation == "night_kill":
                action: Any = NightKill(target=int(target))
            elif obligation == "seer_check":
                action = SeerCheck(target=int(target))
            elif obligation == "doctor_save":
                action = DoctorSave(target=int(target))
            else:
                return _err("not_your_turn", f"seat {seat} owes nothing now")
            table.game.act(seat, action)
        except (IllegalAction, TypeError, ValueError) as e:
            code = e.code if isinstance(e, IllegalAction) else "bad_schema"
            return _err(code, str(e))
        if isinstance(action, SeerCheck):
            return {"ok": True, "role": table.game.role(action.target)}
        return {"ok": True}

    def _tool_wolf_chat(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        try:
            table.game.wolf_chat(seat, str(args.get("msg") or ""))
        except IllegalAction as e:
            return _err(e.code, e.msg)
        return {"ok": True}

    def _tool_read_wolf_chat(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        if table.game.role(seat) != "werewolf":
            return _err("not_your_turn", "werewolves only")
        st = table.seats[seat]
        since = int(args.get("since") or 0)
        chats = [e for e in table.game.events(seat) if e.kind == "wolf_chat"]
        msgs = [
            {"id": i, "seat": e.data["seat"], "day": e.day, "msg": e.data["msg"]}
            for i, e in enumerate(chats)
            if i >= since
        ]
        next_id = len(chats)
        st.since["wolf_chat"] = next_id
        return {"messages": msgs, "next": next_id}

    def _tool_get_budget(self, table: Table, seat: int, args: dict) -> dict:
        spent = table.seats[seat].spent_usd
        limit = table.budget_usd
        return {
            "spent_usd": round(spent, 6),
            "limit_usd": limit,
            "remaining_usd": round(max(0.0, limit - spent), 6),
            "broke": table.is_broke(seat),
        }

    def _check_turn_id(self, table: Table, args: dict) -> CallToolResult | None:
        if args.get("turn_id") != table.game.turn_id:
            return _err("stale_turn", f"current turn is {table.game.turn_id}")
        return None


def _safe_args(args: dict[str, Any]) -> dict[str, Any]:
    # chat text is public-by-design once revealed; keep traces bounded
    out = dict(args)
    for k in ("chat", "msg"):
        if k in out and isinstance(out[k], str) and len(out[k]) > 120:
            out[k] = out[k][:120] + "..."
    return out


def build_mcp_server(state: AppState) -> Server[Any]:
    dispatcher = Dispatcher(state)
    server: Server[Any] = Server("werewolf")

    async def on_list_tools(
        ctx: ServerRequestContext[Any, Any], params: Any
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=name.replace("_", " "),
                    inputSchema={
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                )
                for name, props, required in TOOLS
            ]
        )

    async def on_call_tool(
        ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
    ) -> CallToolResult:
        return await dispatcher.dispatch(ctx, params.name, dict(params.arguments or {}))

    return Server(
        "werewolf",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
