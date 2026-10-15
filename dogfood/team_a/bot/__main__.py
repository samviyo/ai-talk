"""Game loop: wait_for_turn, read_board, one LLM call, act with turn_id.

Maintains a GameState across windows so the model decides from facts, not from a
fresh board dump each time. Code only tracks facts and legal targets; the model
makes every vote/post/night choice.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .agent import (
    BotConfig,
    GameState,
    LLMClient,
    decide_prompt,
    load_config,
    parse_post,
    parse_target,
)

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


def _update_state(state: GameState, announcements: list) -> list[str]:
    """Fold structured announcements into GameState; return text lines for the prompt."""
    out = []
    for a in announcements:
        if not isinstance(a, dict):
            out.append(str(a))
            continue
        kind = a.get("kind")
        if kind == "death":
            state.dead[a["seat"]] = a.get("role", "?")
            out.append(f"seat {a['seat']} died ({a['cause']}); role: {a['role']}")
        elif kind == "no_death":
            out.append(f"nobody died ({a['cause']})")
        elif kind == "seer_result":
            state.my_checks[a["seat"]] = a.get("role", "?")
            out.append(f"your seer check: seat {a['seat']} is {a['role']}")
        elif kind == "phase":
            state.day = a.get("day", state.day)
            out.append(f"phase {a['phase']}, day {a['day']}, round {a['round']}")
        elif kind == "result":
            out.append(f"game result: {a['result']}")
        else:
            out.append(json.dumps(a))
    return out


def _update_from_board(state: GameState, posts: list[dict]) -> None:
    """Track claims and accusations from revealed posts."""
    for p in posts:
        seat = p.get("seat")
        if not isinstance(seat, int):
            continue
        if p.get("claimed_role"):
            state.claims[seat] = p["claimed_role"]
        if isinstance(p.get("accuse"), int):
            state.accusations[seat] = p["accuse"]


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
                state = GameState(
                    seat=role_info["seat"],
                    role=role_info["role"],
                    partners=list(role_info.get("partners") or []),
                )
                print(f"seat {state.seat} role {state.role}", file=sys.stderr)

                while True:
                    if llm.broke:
                        # Out of budget: stop spending; the server fallback acts for us.
                        print("broke: stopping LLM calls", file=sys.stderr)
                        return
                    turn = await _call(session, "wait_for_turn", {"timeout_s": 25})
                    if not isinstance(turn, dict):
                        continue
                    if turn.get("status") == "over":
                        print(f"game over: {turn.get('result')} (llm calls {llm.calls})", file=sys.stderr)
                        return
                    if turn.get("status") == "waiting":
                        continue

                    obligation = turn.get("you_owe")
                    turn_id = turn["turn_id"]
                    legal = turn.get("legal_targets", [])

                    # Only read the board during the day; it costs a tool call.
                    posts: list[dict] = []
                    if turn.get("phase") in ("day_discussion", "day_vote"):
                        board = await _call(session, "read_board", {"since": 0})
                        posts = board.get("posts", []) if isinstance(board, dict) else []
                        _update_from_board(state, posts)
                        state.last_posts = posts
                    else:
                        posts = state.last_posts

                    ann_lines = _update_state(state, turn.get("announcements", []))
                    prompt = decide_prompt(state, turn["phase"], obligation, posts, ann_lines)
                    # Cap the wait so the act call lands inside the window.
                    budget = max(3.0, min(12.0, float(turn.get("deadline_s") or 20.0) - 5.0))
                    llm._client.timeout = budget
                    reply = llm.complete(config.decide_model, prompt)
                    if llm.broke:
                        print("broke: stopping LLM calls", file=sys.stderr)
                        return

                    if obligation == "post":
                        chat, claimed, accuse = parse_post(reply)
                        chat = chat[:280] or "No comment."
                        args = {"turn_id": turn_id, "chat": chat}
                        if claimed:
                            args["claimed_role"] = claimed
                        if isinstance(accuse, int):
                            args["accuse"] = accuse
                        out = await _call(session, "post", args)
                    elif obligation == "vote":
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
