"""Live-chat server rules: budget, power-ups, house costs, call-budget exemption."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from .conftest import mcp_call, start_server


async def _advance_to_discussion(table) -> None:
    async with table.cond:
        g = table.game
        for seat in list(g.pending()):
            g.abstain(seat)
        while g.phase == "reveal":
            g.advance_reveal()
        table.note_window_locked()
        table._released_at -= 0.0
        table._transition_until = 0.0
        assert table.game.phase == "day_discussion"


async def test_wait_and_read_board_exempt_from_call_budget(tmp_path: Path):
    """A 60s discussion of waits/reads never trips the 10-call budget."""
    srv = await start_server(tmp_path, seed=90, window_s=60.0, discussion_s=60.0)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        import time

        for _ in range(15):
            r = await mcp_call(srv.url, srv.token(0), "read_board", {"since": 0})
            if r.get("error") == "rate_limited":
                await asyncio.sleep(1.05)
                continue
            assert "error" not in r, r
            await asyncio.sleep(1.05)
        r = await mcp_call(srv.url, srv.token(0), "get_budget", {})
        assert r.get("error") != "call_budget_exceeded"
        # a budgeted tool (shop) still counts
        for _ in range(10):
            r = await mcp_call(srv.url, srv.token(0), "shop", {})
        r = await mcp_call(srv.url, srv.token(0), "shop", {})
        assert r.get("error") == "call_budget_exceeded"
    finally:
        await srv.stop()


async def test_rate_limit_on_reads(tmp_path: Path):
    srv = await start_server(tmp_path, seed=91, window_s=60.0, discussion_s=60.0)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        r1 = await mcp_call(srv.url, srv.token(1), "read_board", {"since": 0})
        assert "error" not in r1
        r2 = await mcp_call(srv.url, srv.token(1), "read_board", {"since": 0})
        assert r2.get("error") == "rate_limited"
    finally:
        await srv.stop()


async def test_buy_double_vote_then_broke_and_fallback_weight(tmp_path: Path):
    """A seat that buys to broke keeps its double_vote on the fallback ballot."""
    srv = await start_server(tmp_path, seed=92, window_s=60.0, budget=0.20,
                             discussion_s=60.0)
    try:
        table = srv.state.tables[0]
        table.state_house = None
        await _advance_to_discussion(table)
        g = table.game
        alive = g.alive()
        nominator, target = alive[0], alive[1]
        buyer = alive[2]
        tid = table.public_turn_id()
        r = await mcp_call(srv.url, srv.token(nominator), "post", {
            "turn_id": tid, "chat": f"seat {target} is a wolf", "nominate": target,
        })
        assert r.get("ok") is True, r
        # close discussion, skip defence
        async with table.cond:
            g.close_discussion()
            while g.phase == "defence":
                for seat in list(g.pending()):
                    g.abstain(seat)
            table.note_window_locked()
            table._transition_until = 0.0
        assert g.phase == "day_vote"
        # buyer buys double_vote and a shield-sized spend, then votes to broke
        vid = table.public_turn_id()
        r = await mcp_call(srv.url, srv.token(buyer), "buy", {
            "turn_id": vid, "item": "double_vote",
        })
        assert r.get("ok") is True, r
        # force the buyer broke via a simulated charge, then let the clock fall back
        async with table.cond:
            table.charge_locked(buyer, table.budget_usd)
            assert table.is_broke(buyer)
            # everyone else votes; the broke buyer gets the random fallback
            for seat in list(g.pending()):
                if seat != buyer:
                    g.act(seat, __import__("werewolf.engine", fromlist=["Vote"]).Vote(target))
            table._fallback_locked(buyer)
            while g.phase == "reveal":
                g.advance_reveal()
            table.note_window_locked()
        tally = next(e for e in g.events(None) if e.kind == "vote_tally")
        ballot = next(b for b in tally.data["ballots"] if b["seat"] == buyer)
        assert ballot["weight"] == 2, tally.data["ballots"]
        assert ballot["target"] == target  # only standing nominee
    finally:
        await srv.stop()


async def test_buy_errors_and_shop(tmp_path: Path):
    srv = await start_server(tmp_path, seed=93, window_s=60.0, discussion_s=60.0)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        buyer = next(s for s in range(7) if table.game.role(s) != "werewolf")
        tid = table.public_turn_id()
        r = await mcp_call(srv.url, srv.token(buyer), "buy",
                           {"turn_id": tid, "item": "shield"})
        assert r.get("ok") is True, r
        spent0 = table.seats[buyer].spent_usd
        assert spent0 > 0
        r = await mcp_call(srv.url, srv.token(buyer), "buy",
                           {"turn_id": tid, "item": "shield"})
        assert r.get("error") == "already_bought"
        # rejected buys charge nothing
        assert table.seats[buyer].spent_usd == spent0
        r = await mcp_call(srv.url, srv.token(buyer), "buy",
                           {"turn_id": tid, "item": "bogus"})
        assert r.get("error") == "not_available"
        r = await mcp_call(srv.url, srv.token(buyer), "shop", {})
        assert "items" in r
        shield = next(i for i in r["items"] if i["item"] == "shield")
        assert shield["available"] is False
    finally:
        await srv.stop()


async def test_insufficient_budget_rejected(tmp_path: Path):
    srv = await start_server(tmp_path, seed=94, window_s=60.0, budget=0.10,
                             discussion_s=60.0)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        tid = table.public_turn_id()
        r = await mcp_call(srv.url, srv.token(0), "buy",
                           {"turn_id": tid, "item": "shield"})
        assert r.get("error") == "insufficient_budget", r
        assert table.seats[0].spent_usd == 0.0
    finally:
        await srv.stop()


async def test_frame_plus_seer_ordering(tmp_path: Path):
    """A framed villager reads as a wolf to the seer that same night."""
    srv = await start_server(tmp_path, seed=95, window_s=60.0)
    try:
        table = srv.state.tables[0]
        g = table.game
        wolves = [s for s in range(7) if g.role(s) == "werewolf"]
        seer = next(s for s in range(7) if g.role(s) == "seer")
        target = next(
            s for s in range(7)
            if g.role(s) not in ("werewolf", "seer")
        )
        async with table.cond:
            table._transition_until = 0.0
            table._released_at -= 1.0
        r = await mcp_call(srv.url, srv.token(wolves[0]), "buy", {
            "turn_id": table.public_turn_id(), "item": "frame", "target": target,
        })
        assert r.get("ok") is True, r
        r = await mcp_call(srv.url, srv.token(seer), "night_action", {
            "turn_id": table.public_turn_id(), "target": target,
        })
        # deferred: no role in the ack
        assert r.get("ok") is True and "role" not in r, r
        async with table.cond:
            for seat in list(g.pending()):
                g.abstain(seat)
            while g.phase == "reveal":
                g.advance_reveal()
            table.note_window_locked()
        result = next(e for e in g.events(seer) if e.kind == "seer_result")
        assert result.data["role"] == "werewolf"
        assert g.role(target) != "werewolf"
    finally:
        await srv.stop()


async def test_house_seats_charged_simulated_cost(tmp_path: Path):
    """Registering a house seat makes its accepted posts debit the ledger."""
    srv = await start_server(tmp_path, seed=96, window_s=60.0, discussion_s=60.0)
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                srv.url + "/admin/house",
                params={"op": srv.state.operator_token},
                json={"3": "baseline"},
            )
            assert resp.status_code == 200
        table = srv.state.tables[0]
        assert table.seats[3].house == "baseline"
        await _advance_to_discussion(table)
        tid = table.public_turn_id()
        r = await mcp_call(srv.url, srv.token(3), "post",
                           {"turn_id": tid, "chat": "hello from the house"})
        assert r.get("ok") is True, r
        price = srv.state.house_prices["baseline"]
        assert abs(table.seats[3].spent_usd - price) < 1e-9
        # a non-house seat pays nothing for a post
        r = await mcp_call(srv.url, srv.token(4), "post",
                           {"turn_id": tid, "chat": "regular bot"})
        assert table.seats[4].spent_usd == 0.0
        # spectator init labels the house seat
        init = table.spectator_init()
        info = next(s for s in init["seat_info"] if s["seat"] == 3)
        assert info["house"] == "baseline"
        tick = table.spectator_tick()
        entry = next(s for s in tick["seats"] if s["seat"] == 3)
        assert entry["house"] == "baseline"
    finally:
        await srv.stop()


async def test_admin_house_requires_operator(tmp_path: Path):
    srv = await start_server(tmp_path, seed=97, window_s=60.0)
    try:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                srv.url + "/admin/house",
                params={"op": srv.token(0)},  # seat token refused
                json={"0": "baseline"},
            )
            assert resp.status_code == 403
    finally:
        await srv.stop()
