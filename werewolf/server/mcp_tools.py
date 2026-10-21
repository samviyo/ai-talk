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
from .table import NEWS_KINDS, TOOL_CALL_BUDGET, Table

WAIT_DEFAULT_S = 25.0
# Frozen clients use httpx's 5s default read timeout; long-poll must beat it.
WAIT_MAX_S = 3.5
# wait_for_turn and read_board are rate-limited, not counted against the call budget.
RATE_LIMIT_S = 1.0
BUDGET_EXEMPT = {"wait_for_turn", "read_board"}

S = {"type": "integer"}
REASON = {"type": "string", "maxLength": 200}
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
            "reply_to": S,
            "nominate": S,
            "second": S,
            "reason": REASON,
        },
        ["turn_id", "chat"],
    ),
    ("vote", {"turn_id": {"type": "string"}, "seat": S, "reason": REASON}, ["turn_id", "seat"]),
    (
        "night_action",
        {"turn_id": {"type": "string"}, "target": S, "reason": REASON},
        ["turn_id", "target"],
    ),
    ("wolf_chat", {"msg": {"type": "string"}}, ["msg"]),
    ("read_wolf_chat", {"since": S}, []),
    ("get_budget", {}, []),
    ("shop", {}, []),
    (
        "react",
        {"post_id": S, "kind": {"type": "string"}},
        ["post_id", "kind"],
    ),
    (
        "buy",
        {
            "turn_id": {"type": "string"},
            "item": {"type": "string"},
            "target": S,
            "msg": {"type": "string"},
            "reason": REASON,
        },
        ["turn_id", "item"],
    ),
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
        self._rate: dict[tuple[str, int], float] = {}  # last exempt call time

    def _budget_check(
        self, table: Table, seat: int, tool: str
    ) -> CallToolResult | None:
        g = table.game
        key = (table.name, seat)
        if tool == "wait_for_turn":
            return None
        # Reset the counter only when the window changed; get_my_role must not.
        turn_id, count = self.calls.get(key, (g.turn_id, 0))
        if turn_id != g.turn_id:
            turn_id, count = g.turn_id, 0
        if count >= TOOL_CALL_BUDGET:
            return _err("call_budget_exceeded")
        self.calls[key] = (turn_id, count + 1)
        return None

    def _rate_check(self, table: Table, seat: int) -> CallToolResult | None:
        key = (table.name, seat)
        now = time.monotonic()
        last = self._rate.get(key, 0.0)
        if now - last < RATE_LIMIT_S:
            return _err("rate_limited", "read_board is limited to 1/s")
        self._rate[key] = now
        return None

    @staticmethod
    def _gate_err(table: Table) -> CallToolResult | None:
        if table.frozen or table.holding():
            return _err("not_your_turn", "window not open yet")
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
                # In the idle gap, this call opens the next game for this seat.
                if table.idle and not table.manual_start:
                    table.start_game_locked()
                if table.manual_start and not table.awaiting_start:
                    # a running game: re-polls after game_start fetch the dealt role
                    return self._role(table, seat)
                table.mark_ready_locked(seat)
                if len(table.ready_seats) == table.config.seats:
                    table.ready_event.set()
                    table.unfreeze_locked()
                    table.cond.notify_all()
                return self._role(table, seat)
            if over and name in ("post", "vote", "night_action", "wolf_chat", "buy", "react"):
                return _err("not_your_turn", "game over")
            if not over:
                if name in ("post", "vote", "night_action", "react"):
                    gate = self._gate_err(table)
                    if gate is not None:
                        return gate
                if name in BUDGET_EXEMPT:
                    rate = self._rate_check(table, seat)
                    if rate is not None:
                        return rate
                else:
                    budget_err = self._budget_check(table, seat, name)
                    if budget_err is not None:
                        return budget_err
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return _err("bad_schema", f"unknown tool {name}")
            result = handler(table, seat, args)
            table.note_window_locked()
            table._write_run_if_done_locked()
            table._notify_sse_locked()
            table.cond.notify_all()
            return result

    # --- individual tools ----------------------------------------------------

    async def _wait_for_turn(
        self, table: Table, seat: int, args: dict[str, Any]
    ) -> dict[str, Any]:
        timeout = min(float(args.get("timeout_s") or WAIT_DEFAULT_S), WAIT_MAX_S)
        since = args.get("since")
        since = int(since) if since is not None else None
        deadline = time.monotonic() + timeout

        def _remaining() -> float:
            return deadline - time.monotonic()

        async with table.cond:
            while True:
                await table.check_clock_locked()
                g = table.game
                table._notify_sse_locked()
                if table.awaiting_start and not table._game_started:
                    # lobby: no game has begun yet; wake on /admin/start
                    if _remaining() <= 0:
                        return {"status": "waiting", "lobby": True}
                    try:
                        await asyncio.wait_for(table.cond.wait(), timeout=_remaining())
                    except TimeoutError:
                        return {"status": "waiting", "lobby": True}
                    continue
                if g.result is not None:
                    if table.idle:
                        # Each seat hears "over" once, then the gap reports idle.
                        if table._served_turn.get(seat) != "over":
                            table._served_turn[seat] = "over"
                            return {"status": "over", "result": g.result}
                        # Between games: the next one opens on get_my_role readiness.
                        return {"status": "idle", "result": g.result}
                    return {"status": "over", "result": g.result}
                st = table.seats[seat]
                if since is None:
                    st.since["news"] = len(g.events(seat))
                    news = None
                else:
                    # The client cursor indexes the seat's event view.
                    st.since["news"] = max(st.since["news"], since)
                    news = self._news_locked(table, seat, st.since["news"])
                    if news:
                        st.since["news"] = news["cursor"]
                # Obligations beat news: reply and hot-seat first, then the rest.
                owe = None
                owe_extra: dict[str, Any] = {}
                hot = table.hot_seat_prompt_locked(seat)
                # Right of reply only binds while the discussion floor is open.
                reply = g.reply_owed(seat) if g.phase == "day_discussion" else None
                if reply is not None:
                    owe, owe_extra = "reply", reply
                elif hot is not None:
                    owe, owe_extra = "hot_seat", hot
                if owe is not None:
                    out: dict[str, Any] = {
                        "turn_id": table.public_turn_id(),
                        "phase": g.phase,
                        "day": g.day,
                        "round": g.round,
                        "you_owe": owe,
                        "legal_targets": [],
                        "announcements": table.announcements_locked(seat),
                        "posts_left": g.posts_left(seat),
                        "deadline_s": round(table.deadline() - time.monotonic(), 2),
                        **owe_extra,
                    }
                    if news:
                        out.update(news)
                    return out
                if g.phase == "day_discussion":
                    # Live chat: wake on an obligation-free discuss turn or new board.
                    serve_key = f"discuss:{g.turn_id}"
                    cursor = g._day_cursors.get(seat, 0)
                    mentions = g.unread_mentions(seat, cursor)
                    new_posts = len(g._posts) > table.seats[seat].since["board"]
                    first = table._served_turn.get(seat) != serve_key
                    if not table.frozen and not table.holding() and (
                        first or mentions or new_posts
                    ):
                        table._served_turn[seat] = serve_key
                        if first:
                            cursor = len(g._posts)  # opener wake: cursor to now
                            g._day_cursors[seat] = cursor
                        out = {
                            "turn_id": table.public_turn_id(),
                            "phase": g.phase,
                            "day": g.day,
                            "round": g.round,
                            "you_owe": "discuss",
                            "legal_targets": [],
                            "alive": g.alive(),
                            "announcements": table.announcements_locked(seat),
                            "mentions": mentions,
                            "board_cursor": cursor,
                            "posts_left": g.posts_left(seat),
                            "deadline_s": round(table.deadline() - time.monotonic(), 2),
                        }
                        if news:
                            out.update(news)
                        return out
                else:
                    owe = g.pending().get(seat)
                    # Serve each window once per seat; re-polls wait for the next.
                    if (
                        owe is not None
                        and not table.frozen
                        and not table.holding()
                        and table._served_turn.get(seat) != g.turn_id
                    ):
                        table._served_turn[seat] = g.turn_id
                        out = {
                            "turn_id": table.public_turn_id(),
                            "phase": g.phase,
                            "day": g.day,
                            "round": g.round,
                            "you_owe": owe,
                            "legal_targets": g.legal_targets(seat),
                            "announcements": table.announcements_locked(seat),
                            "deadline_s": round(table.deadline() - time.monotonic(), 2),
                        }
                        if news:
                            out.update(news)
                        return out
                if news:
                    return news
                now = time.monotonic()
                remaining = deadline - now
                if table.frozen:
                    pass  # only a notify (unfreeze) or the caller's budget ends this
                elif table.holding():
                    # Wake at release even if the caller's wait budget is longer.
                    remaining = max(0.01, min(remaining, table.release_at() - now + 0.01))
                if remaining <= 0:
                    return {"status": "waiting"}
                try:
                    await asyncio.wait_for(table.cond.wait(), timeout=remaining)
                except TimeoutError:
                    # Never outlive the caller's budget: clients' HTTP read timeout is ~5s.
                    if time.monotonic() < deadline:
                        continue  # re-check after a hold boundary
                    return {"status": "waiting"}

    def _news_locked(
        self, table: Table, seat: int, since: int
    ) -> dict[str, Any] | None:
        """Public events after the seat's own view cursor `since`."""
        g = table.game
        events = g.events(seat)
        if since >= len(events):
            return None
        hits: list = []
        cursor = len(events)
        for i in range(since, len(events)):
            e = events[i]
            if e.audience is None and e.kind in NEWS_KINDS:
                hits.append(e)
                if len(hits) == 10:
                    cursor = i + 1  # resume after the last delivered hit, not past unread ones
                    break
        if not hits:
            return None
        return {
            "status": "news",
            "events": [e.to_dict() for e in hits],
            "cursor": cursor,
        }

    def _role(self, table: Table, seat: int) -> dict[str, Any]:
        g = table.game
        if table.awaiting_start and not table._game_started:
            # lobby placeholder: the role card is dealt only once the game starts
            return {"seat": seat, "team": table.seats[seat].team, "role": None, "partners": [], "lobby": True}
        role = g.role(seat)
        partners: list[int] = []
        if role == "werewolf":
            partners = [
                s for s in range(g.config.seats) if s != seat and g.role(s) == "werewolf"
            ]
        return {"seat": seat, "team": table.seats[seat].team, "role": role, "partners": partners}

    def _tool_get_my_role(self, table: Table, seat: int, args: dict) -> dict:
        return self._role(table, seat)

    def _tool_read_board(self, table: Table, seat: int, args: dict) -> dict:
        st = table.seats[seat]
        since = int(args.get("since") or 0)
        state = table.game.board_state(seat, since)
        st.since["board"] = state["next"]
        out = {
            "posts": state["posts"],
            "next": state["next"],
            "nominations": state["nominations"],
            "standing": state["standing"],
            "posts_left": state["posts_left"],
            "whispers": table.whispers_locked(seat),
        }
        # A reply grant only binds while the discussion floor is open.
        reply = state.get("reply_owed") if table.game.phase == "day_discussion" else None
        if reply is not None:
            out["you_owe"] = "reply"
            out["reply_to"] = reply["reply_to"]
            out["from"] = reply["from"]
            out["chat"] = reply["chat"]
        elif table.game.phase == "day_discussion":
            hot = table.hot_seat_prompt_locked(seat)
            if hot is not None:
                out["you_owe"] = "hot_seat"
                out["prompt"] = hot["prompt"]
        if table.game.phase == "day_discussion":
            out["discussion_ends_in_s"] = round(
                max(0.0, table.deadline() - time.monotonic()), 2
            )
        return out

    def _tool_post(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        if table.is_broke(seat):
            return _err("broke", "seat budget exhausted")
        stale = self._check_turn_id(table, args)
        if stale is not None:
            return stale
        action = Post(
            chat=str(args.get("chat") or ""),
            claimed_role=args.get("claimed_role"),
            accuse=args.get("accuse"),
            reply_to=args.get("reply_to"),
            nominate=args.get("nominate"),
            second=args.get("second"),
        )
        if table.game.phase == "day_discussion":
            # The floor queues accepted posts; visibility lands on release.
            charge = self._house_price(table, seat, "post")
            out = table.submit_discussion_post_locked(seat, action, charge)
            if "error" in out:
                return _err(out["error"], out.get("message", out["error"]))
            table.record_thought_locked(seat, args.get("reason"))
            return out
        try:
            table.game.act(seat, action, now=time.monotonic())
        except IllegalAction as e:
            return _err(e.code, e.msg)
        table.record_thought_locked(seat, args.get("reason"))
        self._charge_house(table, seat, "post")
        return {"ok": True, "posts_left": table.game.posts_left(seat)}

    def _tool_react(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        if table.is_broke(seat):
            return _err("broke", "seat budget exhausted")
        try:
            table.game.react(
                seat, int(args.get("post_id")), str(args.get("kind") or "")
            )
        except (IllegalAction, TypeError, ValueError) as e:
            code = e.code if isinstance(e, IllegalAction) else "bad_schema"
            return _err(code, str(e))
        return {"ok": True}

    def _house_price(self, table: Table, seat: int, action: str) -> float:
        brain = table.seats[seat].house
        if not brain:
            return 0.0
        return float(self.state.house_prices.get(brain, 0.0))

    def _tool_vote(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        if table.is_broke(seat):
            return _err("broke", "seat budget exhausted")
        stale = self._check_turn_id(table, args)
        if stale is not None:
            return stale
        table.record_thought_locked(seat, args.get("reason"))
        try:
            table.game.act(seat, Vote(target=int(args.get("seat"))))
        except (IllegalAction, TypeError, ValueError) as e:
            code = e.code if isinstance(e, IllegalAction) else "bad_schema"
            return _err(code, str(e))
        self._charge_house(table, seat, "vote")
        return {"ok": True}

    def _tool_night_action(
        self, table: Table, seat: int, args: dict
    ) -> dict | CallToolResult:
        if table.is_broke(seat):
            return _err("broke", "seat budget exhausted")
        stale = self._check_turn_id(table, args)
        if stale is not None:
            return stale
        table.record_thought_locked(seat, args.get("reason"))
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
        self._charge_house(table, seat, "night_action")
        # With frames, every seer result is deferred to night resolution.
        return {"ok": True}

    def _tool_wolf_chat(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        if table.is_broke(seat):
            return _err("broke", "seat budget exhausted")
        try:
            table.game.wolf_chat(seat, str(args.get("msg") or ""))
        except IllegalAction as e:
            return _err(e.code, e.msg)
        self._charge_house(table, seat, "wolf_chat")
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

    def _tool_shop(self, table: Table, seat: int, args: dict) -> dict:
        items = table.game.shop(seat)
        for entry in items:
            override = self.state.powerup_prices.get(entry["item"])
            if override is not None:
                entry["price_usd"] = override
        spent = table.seats[seat].spent_usd
        remaining = max(0.0, table.budget_usd - spent)
        for entry in items:
            if entry["available"] and entry["price_usd"] > remaining:
                entry["available"] = False
                entry["reason"] = "insufficient_budget"
        return {"items": items, "remaining_usd": round(remaining, 6)}

    def _tool_buy(self, table: Table, seat: int, args: dict) -> dict | CallToolResult:
        if table.is_broke(seat):
            return _err("broke", "seat budget exhausted")
        stale = self._check_turn_id(table, args)
        if stale is not None:
            return stale
        item = str(args.get("item") or "")
        price = self.state.powerup_prices.get(item)
        if price is None:
            return _err("not_available", f"unknown item {item!r}")
        spent = table.seats[seat].spent_usd
        remaining = table.budget_usd - spent
        if price > remaining + 1e-9:
            # Rejected buys charge nothing; the engine never sees them.
            return _err("insufficient_budget", f"{item} costs ${price:.2f}")
        table.record_thought_locked(seat, args.get("reason"))
        try:
            table.game.apply_powerup(
                seat,
                item,
                target=args.get("target"),
                msg=args.get("msg"),
            )
        except IllegalAction as e:
            return _err(e.code, e.msg)
        table.charge_locked(seat, price)
        # stamp the price onto the powerup event for game.json/SSE
        for e in reversed(table.game._events):
            if e.kind == "powerup" and e.data.get("seat") == seat:
                e.data["usd"] = price
                break
        return {"ok": True, "remaining_usd": round(table.budget_usd - table.seats[seat].spent_usd, 6)}

    def _charge_house(self, table: Table, seat: int, action: str) -> None:
        """House bots pay a simulated price per accepted action."""
        brain = table.seats[seat].house
        if not brain:
            return
        price = self.state.house_prices.get(brain, 0.0)
        if price:
            table.charge_locked(seat, price)

    def _check_turn_id(self, table: Table, args: dict) -> CallToolResult | None:
        # The raw engine id is also accepted for one game back of compatibility.
        current = table.public_turn_id()
        offered = args.get("turn_id")
        if offered != current and offered != table.game.turn_id:
            return _err("stale_turn", f"current turn is {current}")
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
