"""MCP client loop driving one house bot against a game server.

House bots stay well under the 10-calls-per-window budget: reads and waits are
rate-limited instead. Everything from the board is untrusted data; brains read
only its structured fields.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .brains import make_brain

WAIT_TIMEOUT_S = 25
POLL_GAP_S = 1.05  # just over the server's 1/s rate limit


async def _call(session: ClientSession, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(tool, args)
    if result.structured_content is not None:
        return dict(result.structured_content)
    if result.content:
        text = getattr(result.content[0], "text", "") or ""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
    return {}


def _update_dead(view: dict[str, Any], turn: dict[str, Any]) -> None:
    for a in turn.get("announcements") or []:
        if not isinstance(a, dict):
            continue
        if a.get("kind") == "death" and isinstance(a.get("seat"), int):
            view["dead"][a["seat"]] = a.get("role")
        elif a.get("kind") == "seer_result" and isinstance(a.get("seat"), int):
            view["my_checks"][a["seat"]] = a.get("role")
        elif a.get("kind") == "whisper":
            view.setdefault("whispers", []).append(a)


async def _refresh_board(session: ClientSession, view: dict[str, Any], since: int) -> int:
    board = await _call(session, "read_board", {"since": since})
    if "error" in board:
        return since
    view["board"] = (view["board"] or []) + list(board.get("posts") or [])
    view["nominations"] = board.get("nominations") or []
    view["standing"] = board.get("standing") or []
    view["posts_left"] = board.get("posts_left", 0)
    if board.get("discussion_ends_in_s") is not None:
        view["discussion_ends_in_s"] = board["discussion_ends_in_s"]
    for w in board.get("whispers") or []:
        view.setdefault("whispers", []).append(w)
    return int(board.get("next", since))


async def run_bot(server_url: str, token: str, brain_name: str, seed: int) -> dict[str, Any]:
    brain = make_brain(brain_name, seed)
    url = server_url.rstrip("/") + "/mcp"
    final: dict[str, Any] = {"status": "disconnected"}

    # The MCP stream can tear when the server restarts a game; reconnect.
    for _ in range(8):
        http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
        try:
            async with streamable_http_client(url, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    final = await _run_session(session, brain)
            break
        except (BrokenPipeError, ConnectionError, httpx.HTTPError, ExceptionGroup) as e:
            if not _is_stream_break(e):
                raise
            await asyncio.sleep(0.3)
            continue
    return final


def _is_stream_break(exc: BaseException) -> bool:
    """A BrokenResourceError inside anyio's task group means the stream died."""
    names = ("BrokenResourceError", "RemoteProtocolError", "ReadError")
    if type(exc).__name__ in names:
        return True
    if isinstance(exc, ExceptionGroup):
        return any(_is_stream_break(e) for e in exc.exceptions)
    return False


async def _run_session(session: ClientSession, brain: Any) -> dict[str, Any]:
    role_info = await _call(session, "get_my_role", {})
    if not isinstance(role_info, dict) or "seat" not in role_info:
        return {"status": "disconnected", "role_info": role_info}
    view: dict[str, Any] = {
        "seat": role_info.get("seat"),
        "role": role_info.get("role"),
        "partners": role_info.get("partners") or [],
        "turn": None,
        "board": [],
        "nominations": [],
        "standing": [],
        "posts_left": 0,
        "wolf_chat": [],
        "my_checks": {},
        "dead": {},
        "whispers": [],
    }
    cursor = 0
    final: dict[str, Any] = {"status": "disconnected"}
    handoff: dict[str, Any] | None = None  # a turn the discussion loop already consumed

    while True:
        turn = handoff or await _call(session, "wait_for_turn", {"timeout_s": WAIT_TIMEOUT_S})
        handoff = None
        status = turn.get("status")
        if status == "over":
            final = turn
            break
        if status in ("waiting", "idle") or not turn.get("turn_id"):
            continue

        if view["role"] is None:
            # dealt in the lobby: the role card arrives once the operator starts
            role_info = await _call(session, "get_my_role", {})
            if isinstance(role_info, dict) and role_info.get("role"):
                view["role"] = role_info["role"]
                view["partners"] = role_info.get("partners") or []
        view["turn"] = turn
        _update_dead(view, turn)
        phase = turn.get("phase")

        if phase == "night" and view["role"] == "werewolf":
            chat = await _call(session, "read_wolf_chat", {"since": 0})
            view["wolf_chat"] = chat.get("messages") or []

        if phase == "day_discussion":
            cursor, handoff = await _run_discussion(session, view, brain, turn, cursor)
            continue

        # night, defence, vote: one decision per window
        if phase in ("defence", "day_vote"):
            cursor = await _refresh_board(session, view, cursor)
        action = brain.decide(view)
        if action is None:
            continue
        await _do(session, view, turn, action)

    return final


async def _do(session: ClientSession, view: dict, turn: dict, action: dict) -> dict:
    args = dict(action.get("args") or {})
    if action["tool"] in ("post", "vote", "night_action", "buy"):
        args["turn_id"] = turn["turn_id"]
        if action.get("reason"):
            args["reason"] = action["reason"]
    result = await _call(session, action["tool"], args)
    if action["tool"] == "night_action" and view["role"] == "seer":
        # deferred: the result arrives as a seer_result announcement
        pass
    return result


async def _run_discussion(
    session: ClientSession,
    view: dict[str, Any],
    brain: Any,
    turn: dict[str, Any],
    cursor: int,
) -> tuple[int, dict[str, Any] | None]:
    """Live-chat loop: staggered opener, then react to mentions and the board."""
    seat = view["seat"]
    opener_at = brain.opener_delay()
    deadline_s = float(turn.get("deadline_s") or 0)
    started = asyncio.get_event_loop().time()
    opened = False
    reserve = 1  # keep one post for replies
    news_cursor = int(turn.get("board_cursor") or 0)  # event cursor for since=

    while True:
        cursor = await _refresh_board(session, view, cursor)
        view["turn"] = turn
        elapsed = asyncio.get_event_loop().time() - started
        left = deadline_s - elapsed
        view["time_left_s"] = left
        if left <= 0.5:
            return cursor, None
        mentions = turn.get("mentions") or []
        view["mentions"] = mentions
        posts_left = view.get("posts_left", 0)
        action = None
        owe = turn.get("you_owe")
        if owe == "reply":
            action = brain.decide(view)  # free; does not spend a post
        elif owe == "hot_seat":
            action = brain.decide(view)
        elif not opened and elapsed >= opener_at and posts_left > reserve:
            action = brain.decide({**view, "intent": "opener"})
            opened = True
        elif mentions and posts_left > 0:
            action = brain.decide({**view, "intent": "reply", "mentions": mentions})
        elif posts_left > reserve:
            action = brain.decide({**view, "intent": "talk"})
        if action is not None:
            args = dict(action.get("args") or {})
            if action["tool"] != "react":
                args["turn_id"] = turn["turn_id"]
            if action.get("reason"):
                args["reason"] = action["reason"]
            result = await _call(session, action["tool"], args)
            if result.get("error") in (
                "too_fast", "no_posts_left", "broke", "stale_turn", "still_queued",
            ):
                await asyncio.sleep(POLL_GAP_S)
            cursor = await _refresh_board(session, view, cursor)
        # power-up purchase: at most one consideration per discussion
        buy = brain.maybe_buy(view)
        if buy is not None:
            args = dict(buy.get("args") or {})
            args["turn_id"] = turn["turn_id"]
            if buy.get("reason"):
                args["reason"] = buy["reason"]
            await _call(session, "buy", args)
        nxt = await _call(
            session,
            "wait_for_turn",
            {"timeout_s": min(2.0, left), "since": news_cursor},
        )
        if nxt.get("status") == "over":
            return cursor, nxt
        if nxt.get("cursor") is not None:
            news_cursor = int(nxt["cursor"])
        if nxt.get("status") == "news":
            # one batched reaction decision per news wake
            reaction = brain.decide({
                **view, "intent": "news",
                "turn": {**turn, "news_events": nxt.get("events") or []},
            })
            if reaction is not None and reaction["tool"] == "react":
                await _call(session, "react", dict(reaction.get("args") or {}))
        # keep the discussion turn_id; only swap when a real turn payload arrives
        if nxt.get("turn_id"):
            if nxt.get("phase") != "day_discussion":
                return cursor, nxt  # the outer loop must act on this already-served turn
            turn = nxt
            view["turn"] = turn
            _update_dead(view, turn)
        await asyncio.sleep(POLL_GAP_S)
