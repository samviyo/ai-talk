"""End-to-end: a full practice game through the real HTTP/MCP surface."""

from __future__ import annotations

import asyncio
import json


from pathlib import Path

import httpx
import pytest

from werewolf.housebots.runner import run_bot

from .conftest import start_server, wait_turn

BRAINS = ["baseline", "baseline", "accuser", "quiet", "random", "baseline", "baseline"]


async def test_full_game_with_house_bots(tmp_path: Path):
    srv = await start_server(tmp_path, seed=42, window_s=0.5, discussion_s=6.0)
    try:
        # ww table registers house seats through the operator endpoint
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                srv.url + "/admin/house",
                params={"op": srv.state.operator_token},
                json={str(i): BRAINS[i] for i in range(7)},
            )
            assert resp.status_code == 200
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    run_bot(srv.url, srv.token(i), brain, 100 + i)
                    for i, brain in enumerate(BRAINS)
                )
            ),
            timeout=180,
        )
        assert all(r.get("status") == "over" for r in results), results
        game_json = tmp_path / "runs" / "table1" / "game1" / "game.json"
        assert game_json.exists()
        data = json.loads(game_json.read_text())
        assert data["seats"] == 7
        assert data["result"] in ("village", "wolves", "draw")
        assert len(data["roles"]) == 7
        assert data["events"], "no events recorded"
        assert (tmp_path / "runs" / "table1" / "game1" / "trace.jsonl").exists()

        # house map recorded, and every house seat paid its simulated price
        assert data["house"] == {str(i): BRAINS[i] for i in range(7)}
        assert any(v > 0 for v in data["spend"].values()), "house seats never charged"

        # live-chat shape: posts by many seats, at least one nomination, a vote
        posts = [e for e in data["events"] if e["kind"] == "post"]
        day1 = [p for p in posts if p["day"] == 1]
        assert len({p["seat"] for p in day1}) >= 3, "expected several seats posting"
        assert any(e["kind"] == "nomination" for e in data["events"])
        votes = [e for e in data["events"] if e["kind"] == "vote"]
        assert votes or any(e["kind"] == "vote_skipped" for e in data["events"])
        assert all(
            "weight" in v for v in votes
        ), "vote events must carry the double_vote weight"

        # the team-facing eval harness must accept what we wrote
        import sys as _sys

        starter = str(Path(__file__).resolve().parents[3] / "starter")
        _sys.path.insert(0, starter)
        try:
            from evals.trace import load_game

            traces, log = load_game(game_json.parent)
            assert traces, "no trace lines"
            assert log.result == data["result"]
            assert log.seats == 7
            assert sorted(log.wolves) == sorted(
                s for s, r in log.roles.items() if r == "werewolf"
            )
            assert log.house == {i: BRAINS[i] for i in range(7)}
        finally:
            _sys.path.remove(starter)
    finally:
        await srv.stop()


async def test_no_nominee_day_skips_vote(tmp_path: Path):
    """All-quiet discussion: the vote is skipped and the game moves to night."""
    srv = await start_server(tmp_path, seed=43, window_s=0.5, discussion_s=1.0)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(run_bot(srv.url, srv.token(i), "quiet", 200 + i) for i in range(7))
            ),
            timeout=180,
        )
        assert all(r.get("status") == "over" for r in results), results
        data = json.loads(
            (tmp_path / "runs" / "table1" / "game1" / "game.json").read_text()
        )
        assert data["result"] in ("village", "wolves", "draw")
        assert any(e["kind"] == "vote_skipped" for e in data["events"])
    finally:
        await srv.stop()


async def test_stale_and_not_your_turn(tmp_path: Path):
    srv = await start_server(tmp_path, seed=7, window_s=60.0)
    try:
        from .conftest import mcp_call

        table = srv.state.tables[0]
        wolves = [s for s in range(7) if table.game.role(s) == "werewolf"]
        villager = next(s for s in range(7) if table.game.role(s) == "villager")

        # a villager owes nothing at night
        r = await mcp_call(srv.url, srv.token(villager), "night_action", {
            "turn_id": table.game.turn_id, "target": 0,
        })
        assert r["error"] == "not_your_turn"

        # stale turn id
        r = await mcp_call(srv.url, srv.token(wolves[0]), "night_action", {
            "turn_id": "night:0:0", "target": villager,
        })
        assert r["error"] == "stale_turn"

        # wolf_chat refused for non-wolves
        r = await mcp_call(srv.url, srv.token(villager), "wolf_chat", {"msg": "hi"})
        assert r["error"] == "not_your_turn"
        r = await mcp_call(srv.url, srv.token(villager), "read_wolf_chat", {})
        assert r["error"] == "not_your_turn"

        # seat token refused on /events
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                srv.url + "/events", params={"op": srv.token(villager)}
            )
            assert resp.status_code == 403
            resp = await client.get(srv.url + "/")
            assert resp.status_code == 403
            resp = await client.get(
                srv.url + "/", params={"op": srv.state.operator_token}
            )
            assert resp.status_code == 200
    finally:
        await srv.stop()


async def test_call_budget_exceeded(tmp_path: Path):
    srv = await start_server(tmp_path, seed=9, window_s=60.0)
    try:
        from .conftest import mcp_call

        table = srv.state.tables[0]
        # fast-forward the night to reach a discussion window where all owe posts
        async with table.cond:
            g = table.game
            for seat in list(g.pending()):
                g.abstain(seat)  # night resolves
        assert table.game.phase == "day_discussion"
        seat = 0
        turn_id = table.game.turn_id
        last = None
        for i in range(12):
            last = await mcp_call(srv.url, srv.token(seat), "shop", {})
            if last.get("error") == "call_budget_exceeded":
                break
        assert last is not None and last.get("error") == "call_budget_exceeded", last
    finally:
        await srv.stop()
