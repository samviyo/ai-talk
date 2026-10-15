"""LLM proxy and budget/fallback behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from .conftest import PRICES, mcp_call, start_server


def llm_payload(model: str = "fake-model", max_tokens: int | None = 128) -> dict:
    p: dict = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    if max_tokens is not None:
        p["max_tokens"] = max_tokens
    return p


async def post_llm(url: str, token: str, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(
            url + "/llm/v1/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )


async def test_model_not_allowed_and_max_tokens(tmp_path: Path):
    srv = await start_server(tmp_path, seed=5, window_s=60.0)
    try:
        r = await post_llm(srv.url, srv.token(0), llm_payload(model="gpt-99"))
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "model_not_allowed"

        r = await post_llm(srv.url, srv.token(0), llm_payload(max_tokens=None))
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "max_tokens_required"

        r = await post_llm(srv.url, srv.token(0), llm_payload(max_tokens=99999))
        assert r.status_code == 400

        r = await post_llm(srv.url, srv.token(0), llm_payload())
        assert r.status_code == 200
        assert r.json()["usage"]["prompt_tokens"] > 0
    finally:
        await srv.stop()


async def test_concurrent_call_409(tmp_path: Path):
    gate = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0,
            "model": "fake-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return handler(request)

    transport = _AsyncTransport(slow_handler)
    srv = await start_server(
        tmp_path, seed=6, window_s=60.0, fake_llm=False, transport=transport
    )
    try:
        async with httpx.AsyncClient() as client:
            t1 = asyncio.create_task(client.post(
                srv.url + "/llm/v1/chat/completions", json=llm_payload(),
                headers={"Authorization": f"Bearer {srv.token(0)}"}, timeout=30,
            ))
            await asyncio.sleep(0.3)
            r2 = await client.post(
                srv.url + "/llm/v1/chat/completions", json=llm_payload(),
                headers={"Authorization": f"Bearer {srv.token(0)}"}, timeout=30,
            )
            assert r2.status_code == 409, r2.text
            gate.set()
            r1 = await t1
            assert r1.status_code == 200
    finally:
        await srv.stop()


class _AsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


async def test_broke_after_limit_and_fallback(tmp_path: Path):
    # tiny budget: one fake-LLM call busts it
    srv = await start_server(tmp_path, seed=8, window_s=0.3, budget=0.000001)
    try:
        r = await post_llm(srv.url, srv.token(0), llm_payload())
        assert r.status_code == 200
        r = await post_llm(srv.url, srv.token(0), llm_payload())
        assert r.status_code == 402, r.text

        budget = await mcp_call(srv.url, srv.token(0), "get_budget", {})
        assert budget["broke"] is True

        # broke seat may not post
        table = srv.state.tables[0]
        async with table.cond:
            g = table.game
            for s in list(g.pending()):
                g.abstain(s)  # resolve night
        assert table.game.phase == "day_discussion"
        r = await mcp_call(srv.url, srv.token(0), "post", {
            "turn_id": table.game.turn_id, "chat": "hello",
        })
        assert r["error"] == "broke"

        # funded wolf still decides the kill while a broke wolf submits nothing;
        # covered by test_funded_wolf_decides_kill below
    finally:
        await srv.stop()


async def test_funded_wolf_decides_kill(tmp_path: Path):
    srv = await start_server(tmp_path, seed=21, window_s=60.0)
    try:
        table = srv.state.tables[0]
        wolves = [s for s in range(7) if table.game.role(s) == "werewolf"]
        broke_wolf, funded_wolf = wolves[0], wolves[1]
        async with table.cond:
            table.seats[broke_wolf].spent_usd = table.budget_usd  # mark broke
        target = next(
            s for s in table.game.legal_targets(funded_wolf)
        )
        r = await mcp_call(srv.url, srv.token(funded_wolf), "night_action", {
            "turn_id": table.game.turn_id, "target": target,
        })
        assert r.get("ok") is True
        # now let the clock fire: broke wolf must abstain, kill still lands
        async with table.cond:
            table._released_at -= 120.0
            table._transition_until = 0.0
            table.window_opened -= 120.0
            await table.check_clock_locked()
        night = [e for e in table.game.events(None) if e.kind == "night_result"][-1]
        assert night.data["kill_target"] == target
    finally:
        await srv.stop()


async def test_fallback_random_when_all_broke(tmp_path: Path):
    srv = await start_server(tmp_path, seed=13, window_s=0.2, budget=0.0)
    try:
        # no bots: every window times out, fallback must still finish the game
        table = srv.state.tables[0]
        for _ in range(600):
            async with table.cond:
                await table.check_clock_locked()
                if table.game.result is not None:
                    break
            await asyncio.sleep(0.25)
        assert table.game.result is not None, "fallback never finished the game"
        g = table.game
        # a kill must have happened on some night (all wolves broke -> random kill)
        night_results = [e for e in g.events(None) if e.kind == "night_result"]
        assert any(e.data["kill_target"] is not None for e in night_results)
        # every seat is broke and silent: days end in vote_skipped
        assert any(e.kind == "vote_skipped" for e in g.events(None))
    finally:
        await srv.stop()
