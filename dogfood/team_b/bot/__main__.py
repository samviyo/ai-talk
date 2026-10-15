"""Game loop: wait_for_turn, read_board, haiku summary, sonnet decision, act."""
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
    LLMClient,
    decide_prompt,
    load_config,
    parse_post,
    parse_target,
    summarize_board,
)

# Error codes that mean "stop acting this window" rather than retry.
_STOP_CODES = {"stale_turn", "already_acted", "broke", "call_budget_exceeded"}


def _text(result) -> str:
    parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    return "\n".join(parts)


async def _call(session: ClientSession, tool: str, args: dict) -> dict | str:
    """Call a game tool; returns parsed JSON or the error-code string."""
    try:
        res = await session.call_tool(tool, args)
    except Exception as exc:  # transport hiccup: treat as skip, never crash the loop
        return f"transport_error:{type(exc).__name__}"
    text = _text(res)
    if res.is_error:
        return text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


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


async def _act_with_retry(session, llm, config, build_prompt, legal, act):
    """Ask sonnet for a target, retrying once on an unparseable reply."""
    for attempt in range(2):
        reply = llm.complete(config.decide_model, build_prompt(attempt))
        if llm.broke:
            return "broke"
        target = parse_target(reply, legal)
        if target is not None:
            return await act(target)
    return None  # abstain: model never produced a legal target


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
                partners = role_info.get("partners") or []
                print(f"seat {seat} role {my_role} partners {partners}", file=sys.stderr)

                last_summary_key = None
                summary = ""

                while True:
                    if llm.broke:
                        # Out of budget: stop spending; the server fallback acts for us.
                        print("broke: stopping LLM calls", file=sys.stderr)
                        return
                    turn = await _call(session, "wait_for_turn", {"timeout_s": 25})
                    if not isinstance(turn, dict):
                        continue
                    if turn.get("status") == "over":
                        print(f"game over: {turn.get('result')}", file=sys.stderr)
                        return
                    if turn.get("status") == "waiting":
                        continue

                    obligation = turn.get("you_owe")
                    turn_id = turn["turn_id"]
                    legal = turn.get("legal_targets", [])
                    phase = turn.get("phase")
                    announcements = _format_announcements(turn.get("announcements", []))

                    # Wolf coordination: read partner chat each night.
                    wolf_notes = ""
                    if my_role == "werewolf" and obligation == "night_kill":
                        wc = await _call(session, "read_wolf_chat", {"since": 0})
                        if isinstance(wc, dict):
                            notes = [m.get("msg", "") for m in wc.get("messages", []) if m.get("seat") != seat]
                            if notes:
                                wolf_notes = "\nPartner wolf chat: " + " | ".join(notes[-3:])

                    board = await _call(session, "read_board", {"since": 0})
                    posts = board.get("posts", []) if isinstance(board, dict) else []

                    # Haiku summary, refreshed when the board grows.
                    key = (len(posts), phase)
                    if posts and key != last_summary_key:
                        summary = summarize_board(llm, posts)
                        last_summary_key = key
                    if wolf_notes:
                        summary = (summary + wolf_notes).strip()

                    if obligation == "post":
                        prompt = decide_prompt(
                            phase, obligation, my_role, posts, legal,
                            announcements, summary, partners, seat,
                        )
                        reply = llm.complete(config.decide_model, prompt)
                        if llm.broke:
                            print("broke: stopping LLM calls", file=sys.stderr)
                            return
                        chat, claimed, accuse = parse_post(reply)
                        args = {"turn_id": turn_id, "chat": chat}
                        if claimed:
                            args["claimed_role"] = claimed
                        if accuse is not None:
                            args["accuse"] = accuse
                        out = await _call(session, "post", args)

                    elif obligation == "vote":
                        def build(attempt):
                            p = decide_prompt(
                                phase, obligation, my_role, posts, legal,
                                announcements, summary, partners, seat,
                            )
                            return p + ("\n\nYour last reply was not valid JSON. Reply with ONLY {\"target\": <seat>}." if attempt else "")

                        async def do_vote(target):
                            return await _call(session, "vote", {"turn_id": turn_id, "seat": target})

                        out = await _act_with_retry(session, llm, config, build, legal, do_vote)

                    elif obligation in ("night_kill", "seer_check", "doctor_save"):
                        def build(attempt):
                            p = decide_prompt(
                                phase, obligation, my_role, posts, legal,
                                announcements, summary, partners, seat,
                            )
                            return p + ("\n\nYour last reply was not valid JSON. Reply with ONLY {\"target\": <seat>}." if attempt else "")

                        async def do_night(target):
                            return await _call(session, "night_action", {"turn_id": turn_id, "target": target})

                        out = await _act_with_retry(session, llm, config, build, legal, do_night)

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
