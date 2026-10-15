"""MCP client loop driving one house bot against a game server.

House bots stay well under the 10-calls-per-window budget: one wait_for_turn
plus at most one read per kind and one action each window. Everything from the
board is treated as untrusted data; brains only read its structured fields.
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


def _record_seer_check(view: dict[str, Any], result: dict[str, Any], args: dict[str, Any]) -> None:
    if view["role"] == "seer" and isinstance(result.get("role"), str):
        view["my_checks"][args["target"]] = result["role"]


async def run_bot(server_url: str, token: str, brain_name: str, seed: int) -> dict[str, Any]:
    brain = make_brain(brain_name, seed)
    url = server_url.rstrip("/") + "/mcp"
    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    final: dict[str, Any] = {"status": "disconnected"}

    async with streamable_http_client(url, http_client=http_client) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            role_info = await _call(session, "get_my_role", {})
            view: dict[str, Any] = {
                "seat": role_info.get("seat"),
                "role": role_info.get("role"),
                "partners": role_info.get("partners") or [],
                "turn": None,
                "board": [],
                "wolf_chat": [],
                "my_checks": {},
                "dead": {},
            }

            while True:
                turn = await _call(session, "wait_for_turn", {"timeout_s": WAIT_TIMEOUT_S})
                status = turn.get("status")
                if status == "over":
                    final = turn
                    break
                if status == "waiting" or not turn.get("turn_id"):
                    continue

                view["turn"] = turn
                _update_dead(view, turn)

                if turn.get("phase") in ("day_discussion", "day_vote"):
                    board = await _call(session, "read_board", {"since": 0})
                    view["board"] = board.get("posts") or []
                if view["role"] == "werewolf" and turn.get("phase") == "night":
                    chat = await _call(session, "read_wolf_chat", {"since": 0})
                    view["wolf_chat"] = chat.get("messages") or []

                action = brain.decide(view)
                if action is None:
                    continue

                args = dict(action.get("args") or {})
                if action["tool"] in ("post", "vote", "night_action"):
                    args["turn_id"] = turn["turn_id"]
                result = await _call(session, action["tool"], args)
                if action["tool"] == "night_action":
                    _record_seer_check(view, result, args)

    return final
