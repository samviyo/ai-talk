"""Game loop: wait_for_turn, read_board, one LLM call, act with turn_id."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import CONNECTION_CLOSED

from .agent import (
    BotConfig, LLMClient, clip, decide_prompt, load_config, opener_prompt, parse_opener,
    parse_target, say,
)

# Error codes that mean "stop acting this window" rather than retry.
_STOP_CODES = {"stale_turn", "already_acted", "broke", "call_budget_exceeded"}
# Tools that change the game; each call is logged with its result.
_LOGGED_TOOLS = {"post", "vote", "night_action", "react", "buy"}
RECONNECT_S = 2.0


def _text(result) -> str:
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return "\n".join(parts)


async def _call(session: ClientSession, tool: str, args: dict) -> dict | str:
    """Call a game tool; returns parsed JSON or the error-code string."""
    res = await session.call_tool(tool, args)
    text = _text(res)
    if res.is_error:
        out: dict | str = text.strip()
    else:
        try:
            out = json.loads(text)
        except json.JSONDecodeError:
            out = text
    if tool in _LOGGED_TOOLS:
        shown = ", ".join(f"{k}={clip(v, 80)!r}" if k == "chat" else f"{k}={v}"
                          for k, v in args.items() if k != "turn_id")
        err = out if isinstance(out, str) else out.get("error")
        say(f"  {tool}({shown}) -> {clip(err, 80) if err else 'ok'}")
    return out


QUEUED_BACKOFF_S = 6.0  # roughly one floor slot; re-posting sooner is refused anyway


def _queued(out: dict | str) -> bool:
    return isinstance(out, dict) and out.get("error") == "still_queued"


def _code(out: dict | str) -> str | None:
    """The server's error code, whether it came back as text or as {"error": ...}."""
    return out.get("error") if isinstance(out, dict) else out


async def _maybe_buy_vote(session: ClientSession, turn: dict) -> None:
    """Example purchase rule: buy a double vote once if we can afford it."""
    shop = await _call(session, "shop", {})
    if not isinstance(shop, dict):
        return
    for item in shop.get("items", []):
        if item.get("item") == "double_vote" and item.get("available"):
            await _call(session, "buy", {"turn_id": turn["turn_id"], "item": "double_vote"})
            return


async def _discuss(session: ClientSession, llm, config: BotConfig,
                   my_role: str, seat: int, partners: list[int], turn: dict) -> dict | None:
    """Live-chat loop; returns a non-discussion turn it already consumed, if any."""
    turn_id = turn["turn_id"]
    cursor = int(turn.get("board_cursor") or 0)
    deadline_s = float(turn.get("deadline_s") or 0)
    started = asyncio.get_event_loop().time()
    opened = False
    reserve = 1
    news_cursor: int | None = None  # event cursor for wait_for_turn(since=...)
    alive = list(turn.get("alive") or [])
    queued_until = 0.0  # a still_queued post holds the floor; resending burns the call budget
    stopped = False  # the server refused a post for the rest of this window
    replies: dict[object, str] = {}  # reply_to -> our text, so a re-served reply costs no model call
    answered: set[object] = set()
    say(f"day {turn.get('day')} discussion: {deadline_s:.0f}s on the clock")
    while True:
        left = deadline_s - (asyncio.get_event_loop().time() - started)
        if left <= 1.0 or llm.broke:
            return None
        board = await _call(session, "read_board", {"since": cursor})
        if not isinstance(board, dict) or "error" in board:
            await asyncio.sleep(1.05)
            continue
        posts = board.get("posts", [])
        cursor = int(board.get("next", cursor))
        posts_left = int(board.get("posts_left", 0))
        nominations = board.get("nominations") or []
        mentions = turn.get("mentions") or []
        want = None
        owe = turn.get("you_owe")
        if asyncio.get_event_loop().time() < queued_until or stopped:
            owe = "wait"  # our last post is still queued for the floor
        key = turn.get("reply_to", turn.get("from"))
        if owe == "reply" and key in answered:
            owe = "wait"
        if owe == "reply" and key in replies:
            out = await _call(session, "post", {"turn_id": turn_id, "chat": replies[key],
                                                **({"reply_to": key} if "reply_to" in turn else {})})
            stopped = _code(out) in _STOP_CODES
            if _queued(out):
                queued_until = asyncio.get_event_loop().time() + QUEUED_BACKOFF_S
            else:
                answered.add(key)
        elif owe == "reply":
            say(f"  seat {turn.get('from')} accused us: {clip(turn.get('chat', ''), 100)!r}; replying")
            # A granted reply is free: it doesn't spend a post.
            prompt = (
                f"Seat {turn.get('from')} accused you: {turn.get('chat', '')!r}\n"
                "Reply in one short line (<=280 chars), nothing else."
            )
            reply = llm.complete(
                config.decide_model, prompt, timeout_s=max(1.0, left - 3.0),
                max_tokens=128,
            )
            if llm.broke:
                return None
            chat = reply[:280] or "I disagree."
            replies[key] = chat
            args: dict = {"turn_id": turn_id, "chat": chat}
            if turn.get("reply_to") is not None:
                args["reply_to"] = turn["reply_to"]
            out = await _call(session, "post", args)
            stopped = _code(out) in _STOP_CODES
            if _queued(out):
                queued_until = asyncio.get_event_loop().time() + QUEUED_BACKOFF_S
            else:
                answered.add(key)
        elif owe == "hot_seat":
            say(f"  hot seat, 5s to answer: {clip(turn.get('prompt', ''), 100)!r}")
            # 5s speed test: a handful of tokens, a tight timeout.
            prompt = (
                f"Hot seat, answer fast, one short line: {turn.get('prompt', '')}"
            )
            reply = llm.complete(
                config.decide_model, prompt, timeout_s=4.0,
                max_tokens=48,
            )
            if llm.broke:
                return None
            chat = reply[:280] or "No comment."
            out = await _call(session, "post", {"turn_id": turn_id, "chat": chat})
            stopped = _code(out) in _STOP_CODES
            if _queued(out):
                queued_until = asyncio.get_event_loop().time() + QUEUED_BACKOFF_S
        elif owe == "wait":
            pass
        else:
            if not opened and posts_left > reserve:
                want = "opener"
            elif mentions and posts_left > 0:
                want = "reply"
            if want:
                say("  writing our opener" if want == "opener"
                    else f"  {len(mentions)} mention(s) of us; writing a reply")
                anns = _format_announcements(turn.get("announcements", []))
                if want == "opener":
                    # The opener carries the nomination: no nominations means no vote today.
                    prompt = opener_prompt(my_role, posts, alive, seat, nominations, anns, partners)
                else:
                    prompt = decide_prompt("day_discussion", "post", my_role, posts, [], anns)
                reply = llm.complete(
                    config.decide_model, prompt, timeout_s=max(1.0, left - 3.0)
                )
                if llm.broke:
                    return None
                post_args: dict = {"turn_id": turn_id}
                if want == "opener":
                    chat, nominate, second = parse_opener(reply, alive, seat, partners)
                    if nominate is not None:
                        post_args["nominate"] = nominate
                    if second is not None and any(n["target"] == second for n in nominations):
                        post_args["second"] = second
                else:
                    chat = reply
                post_args["chat"] = chat[:280] or "No comment."
                out = await _call(session, "post", post_args)
                stopped = _code(out) in _STOP_CODES
                if isinstance(out, dict) and not out.get("error"):
                    opened = True
                elif _queued(out):
                    queued_until = asyncio.get_event_loop().time() + QUEUED_BACKOFF_S
        args = {"timeout_s": 3}
        if news_cursor is not None:
            args["since"] = news_cursor
        turn = await _call(session, "wait_for_turn", args)
        if not isinstance(turn, dict):
            return None
        if turn.get("status") == "over":
            return turn
        if isinstance(turn.get("cursor"), int):
            news_cursor = turn["cursor"]
        if turn.get("status") == "news":
            if not stopped:
                await _on_news(session, llm, config, turn)
            continue
        if turn.get("phase") not in (None, "day_discussion"):
            return turn
        await asyncio.sleep(1.05)


async def _on_news(session: ClientSession, llm, config: BotConfig,
                   news: dict) -> None:
    """At most one LLM call per news batch: react, or ignore."""
    posts = []
    for e in news.get("events") or []:
        if isinstance(e, dict) and e.get("kind") == "posts_revealed":
            posts.extend(e.get("posts") or [])
    posts = [p for p in posts if isinstance(p.get("id"), int)]
    if not posts:
        return
    say(f"  {len(posts)} new post(s) on the board; deciding whether to react")
    # Reactions are cheap; at most one per news batch.
    lines = "\n".join(f"post {p['id']} by seat {p.get('seat')}: {p.get('chat')}"
                      for p in posts[:5])
    prompt = (
        "New board posts:\n" + lines + "\n\n"
        "Reactions are cheap (agree|doubt|suspect). Reply with JSON "
        '{"react": <post_id>, "kind": "agree|doubt|suspect"} or {"react": null}.'
    )
    reply = llm.complete(config.decide_model, prompt, timeout_s=3.0, max_tokens=32)
    if llm.broke or not reply:
        return
    try:
        data = json.loads(reply)
        post_id = data.get("react")
        kind = data.get("kind")
    except (json.JSONDecodeError, AttributeError):
        return
    if isinstance(post_id, int) and kind in ("agree", "doubt", "suspect"):
        await _llm_free_react(session, post_id, kind)


async def _llm_free_react(session: ClientSession, post_id: int, kind: str) -> None:
    await _call(session, "react", {"post_id": post_id, "kind": kind})


def _format_announcements(announcements: list) -> list[str]:
    """Structured announcement dicts -> one-line text for the prompt."""
    out = []
    for a in announcements:
        if not isinstance(a, dict):
            out.append(str(a))
            continue
        kind = a.get("kind")
        if kind == "death":
            out.append(f"seat {a['seat']} died ({a['cause']}); role: {a['role']}")
        elif kind == "no_death":
            out.append(f"nobody died ({a['cause']})")
        elif kind == "seer_result":
            out.append(f"your seer check: seat {a['seat']} is {a['role']}")
        elif kind == "phase":
            out.append(f"phase {a['phase']}, day {a['day']}, round {a['round']}")
        elif kind == "result":
            out.append(f"game result: {a['result']}")
        else:
            out.append(json.dumps(a))
    return out


async def play(server: str, token: str, config: BotConfig, pending: dict | None = None) -> None:
    """One connection's game loop; `pending` carries an unfinished turn across reconnects."""
    pending = {} if pending is None else pending
    llm = LLMClient(server, token, config)
    url = f"{server}/mcp"
    # mcp 2.x: bearer auth rides on an httpx client, not a headers kwarg.
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {token}"}
    ) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                role_info = await _call(session, "get_my_role", {})
                # A rejected token comes back as an error dict, not a role.
                if not isinstance(role_info, dict) or "role" not in role_info:
                    say(f"get_my_role failed: {role_info}")
                    return
                my_role = role_info["role"]
                seat = role_info["seat"]
                team = role_info.get("team") or "?"
                partners = list(role_info.get("partners") or [])
                if my_role is None:
                    say(f"seat {seat} · team {team} · in the lobby, waiting for the start")
                else:
                    _say_role(seat, team, my_role, partners)

                handoff: dict | None = _resume(pending)  # a turn _discuss consumed, or one cut off by a drop
                while True:
                    if llm.broke:
                        # Out of budget: stop spending; the server fallback acts for us.
                        say("broke: stopping LLM calls")
                        return
                    pending.clear()
                    turn = handoff or await _call(session, "wait_for_turn", {"timeout_s": 25})
                    handoff = None
                    if not isinstance(turn, dict):
                        continue
                    if turn.get("status") == "over":
                        say(f"game over: {turn.get('result')}")
                        return
                    if turn.get("status") in ("waiting", "idle"):
                        continue

                    if my_role is None:
                        # joined in the lobby: the role is dealt once the game starts
                        role_info = await _call(session, "get_my_role", {})
                        if isinstance(role_info, dict) and role_info.get("role"):
                            my_role = role_info["role"]
                            partners = list(role_info.get("partners") or [])
                            _say_role(seat, team, my_role, partners)

                    obligation = turn.get("you_owe")
                    turn_id = turn["turn_id"]
                    legal = turn.get("legal_targets", [])
                    for line in _format_announcements(turn.get("announcements", [])):
                        say(f"moderator: {line}")

                    if obligation in ("discuss", "reply", "hot_seat"):
                        if float(turn.get("deadline_s") or 0) <= 1.0:
                            # The floor has closed but the server re-serves the day until it drains.
                            await asyncio.sleep(1.0)
                            continue
                        handoff = await _discuss(session, llm, config, my_role, seat, partners, turn)
                        continue

                    say(f"day {turn.get('day')} {turn.get('phase')}: we owe {obligation}"
                        + (f", targets {legal}" if legal else ""))
                    # The server serves each night/vote turn once, so a reconnect must replay it.
                    pending["turn"] = turn
                    pending["until"] = time.monotonic() + float(turn.get("deadline_s") or 0)
                    board = await _call(session, "read_board", {"since": 0})
                    posts = board.get("posts", []) if isinstance(board, dict) else []
                    prompt = decide_prompt(
                        turn["phase"], obligation, my_role, posts, legal,
                        _format_announcements(turn.get("announcements", [])),
                    )
                    # Bound the LLM call below the window deadline or we abstain the turn.
                    deadline = turn.get("deadline_s")
                    timeout_s = max(1.0, float(deadline) - 3.0) if deadline else None
                    reply = llm.complete(config.decide_model, prompt, timeout_s=timeout_s)
                    if llm.broke:
                        say("broke: stopping LLM calls")
                        return

                    if obligation == "defence":
                        chat = reply[:280] or "I am innocent."
                        out = await _call(session, "post", {"turn_id": turn_id, "chat": chat})
                    elif obligation == "vote":
                        if config.buy_double_vote:
                            await _maybe_buy_vote(session, turn)
                        target = parse_target(reply, legal)
                        if target is None:
                            say("  no legal target in the reply: abstaining")
                            continue  # abstain rather than vote illegally
                        out = await _call(session, "vote", {"turn_id": turn_id, "seat": target})
                    elif obligation in ("night_kill", "seer_check", "doctor_save"):
                        target = parse_target(reply, legal)
                        if target is None:
                            say("  no legal target in the reply: abstaining")
                            continue
                        out = await _call(
                            session, "night_action", {"turn_id": turn_id, "target": target}
                        )
                    else:
                        continue
                    if _code(out) in _STOP_CODES:
                        say(f"  {obligation} rejected: {_code(out)}")


def _say_role(seat: int, team: str, role: str, partners: list[int]) -> None:
    say(f"seat {seat} role {role} · team {team}" + (f" · wolf partners {partners}" if partners else ""))


def _resume(pending: dict) -> dict | None:
    """The turn a dropped connection interrupted, with its deadline shortened, if still open."""
    turn, left = pending.get("turn"), pending.get("until", 0) - time.monotonic()
    if not turn or left <= 1.0:
        return None
    say(f"resuming day {turn.get('day')} {turn.get('phase')} with {left:.0f}s left")
    return {**turn, "deadline_s": left}


def _first_leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup):
        exc = exc.exceptions[0]
    return exc


def _dropped(exc: BaseException) -> bool:
    if isinstance(exc, MCPError):
        return exc.code == CONNECTION_CLOSED
    return isinstance(exc, (httpx.TransportError, ConnectionError))


def _play_once(server: str, token: str, config: BotConfig, pending: dict) -> bool:
    """True when the game ended or the token was refused; False after a dropped connection."""
    dropped: BaseException | None = None
    try:
        asyncio.run(play(server, token, config, pending))
    except* (httpx.TransportError, ConnectionError, MCPError) as group:
        match, rest = group.split(_dropped)
        if rest is not None:
            raise rest
        dropped = _first_leaf(match)
    if dropped is None:
        return True
    say(f"lost the server ({type(dropped).__name__}); reconnecting in {RECONNECT_S:.0f}s")
    return False


def main() -> None:
    server = os.environ.get("WW_SERVER")
    token = os.environ.get("WW_TOKEN")
    if not server or not token:
        sys.exit("WW_SERVER and WW_TOKEN must be set")
    say(f"connecting to {server}")
    pending: dict = {}
    while not _play_once(server.rstrip("/"), token, load_config(), pending):
        time.sleep(RECONNECT_S)


if __name__ == "__main__":
    main()
