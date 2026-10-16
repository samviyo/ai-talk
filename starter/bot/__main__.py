"""Game loop: wait_for_turn, read_board, one LLM call, act with turn_id."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .agent import BotConfig, LLMClient, decide_prompt, load_config, parse_target

# Error codes that mean "stop acting this window" rather than retry.
_STOP_CODES = {"stale_turn", "already_acted", "broke", "call_budget_exceeded"}


def _text(result) -> str:
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return "\n".join(parts)


async def _call(session: ClientSession, tool: str, args: dict) -> dict | str:
    """Call a game tool; returns parsed JSON or the error-code string."""
    res = await session.call_tool(tool, args)
    text = _text(res)
    if res.is_error:
        return text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


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
                   my_role: str, turn: dict) -> dict | None:
    """Live-chat loop; returns a non-discussion turn it already consumed, if any."""
    turn_id = turn["turn_id"]
    cursor = int(turn.get("board_cursor") or 0)
    deadline_s = float(turn.get("deadline_s") or 0)
    started = asyncio.get_event_loop().time()
    opened = False
    reserve = 1
    news_cursor: int | None = None  # event cursor for wait_for_turn(since=...)
    while True:
        left = deadline_s - (asyncio.get_event_loop().time() - started)
        if left <= 1.0 or llm.broke:
            return
        board = await _call(session, "read_board", {"since": cursor})
        if not isinstance(board, dict) or "error" in board:
            await asyncio.sleep(1.05)
            continue
        posts = board.get("posts", [])
        cursor = int(board.get("next", cursor))
        posts_left = int(board.get("posts_left", 0))
        mentions = turn.get("mentions") or []
        want = None
        owe = turn.get("you_owe")
        if owe == "reply":
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
                return
            chat = reply[:280] or "I disagree."
            args: dict = {"turn_id": turn_id, "chat": chat}
            if turn.get("reply_to") is not None:
                args["reply_to"] = turn["reply_to"]
            await _call(session, "post", args)
        elif owe == "hot_seat":
            # 5s speed test: no reasoning, a handful of tokens.
            prompt = (
                f"Hot seat, answer fast, one short line: {turn.get('prompt', '')}"
            )
            reply = llm.complete(
                config.decide_model, prompt, timeout_s=4.0,
                max_tokens=48, fast=True,
            )
            if llm.broke:
                return
            chat = reply[:280] or "No comment."
            await _call(session, "post", {"turn_id": turn_id, "chat": chat})
        else:
            if not opened and posts_left > reserve:
                want = "opener"
            elif mentions and posts_left > 0:
                want = "reply"
            if want:
                prompt = decide_prompt(
                    "day_discussion", "post", my_role, posts, [],
                    _format_announcements(turn.get("announcements", [])),
                )
                reply = llm.complete(
                    config.decide_model, prompt, timeout_s=max(1.0, left - 3.0)
                )
                if llm.broke:
                    return
                chat = reply[:280] or "No comment."
                out = await _call(session, "post", {"turn_id": turn_id, "chat": chat})
                if isinstance(out, dict) and not out.get("error"):
                    opened = True
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


async def play(server: str, token: str, config: BotConfig) -> None:
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
                if not isinstance(role_info, dict):
                    print(f"get_my_role failed: {role_info}", file=sys.stderr)
                    return
                my_role = role_info["role"]
                seat = role_info["seat"]
                print(f"seat {seat} role {my_role}", file=sys.stderr)

                handoff: dict | None = None  # a turn _discuss already consumed
                while True:
                    if llm.broke:
                        # Out of budget: stop spending; the server fallback acts for us.
                        print("broke: stopping LLM calls", file=sys.stderr)
                        return
                    turn = handoff or await _call(session, "wait_for_turn", {"timeout_s": 25})
                    handoff = None
                    if not isinstance(turn, dict):
                        continue
                    if turn.get("status") == "over":
                        print(f"game over: {turn.get('result')}", file=sys.stderr)
                        return
                    if turn.get("status") in ("waiting", "idle"):
                        continue

                    if my_role is None:
                        # joined in the lobby: the role is dealt once the game starts
                        role_info = await _call(session, "get_my_role", {})
                        if isinstance(role_info, dict) and role_info.get("role"):
                            my_role = role_info["role"]
                            print(f"seat {seat} role {my_role}", file=sys.stderr)

                    obligation = turn.get("you_owe")
                    turn_id = turn["turn_id"]
                    legal = turn.get("legal_targets", [])

                    if obligation == "discuss":
                        handoff = await _discuss(session, llm, config, my_role, turn)
                        continue

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
                        print("broke: stopping LLM calls", file=sys.stderr)
                        return

                    if obligation == "defence":
                        chat = reply[:280] or "I am innocent."
                        out = await _call(session, "post", {"turn_id": turn_id, "chat": chat})
                    elif obligation == "vote":
                        if config.buy_double_vote:
                            await _maybe_buy_vote(session, turn)
                        target = parse_target(reply, legal)
                        if target is None:
                            continue  # abstain rather than vote illegally
                        out = await _call(session, "vote", {"turn_id": turn_id, "seat": target})
                    elif obligation in ("night_kill", "seer_check", "doctor_save"):
                        target = parse_target(reply, legal)
                        if target is None:
                            continue
                        out = await _call(
                            session, "night_action", {"turn_id": turn_id, "target": target}
                        )
                    else:
                        continue
                    if isinstance(out, str) and out in _STOP_CODES:
                        print(f"{obligation} rejected: {out}", file=sys.stderr)


def main() -> None:
    server = os.environ.get("WW_SERVER")
    token = os.environ.get("WW_TOKEN")
    if not server or not token:
        sys.exit("WW_SERVER and WW_TOKEN must be set")
    asyncio.run(play(server.rstrip("/"), token, load_config()))


if __name__ == "__main__":
    main()
