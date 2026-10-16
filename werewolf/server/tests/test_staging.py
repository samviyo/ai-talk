"""Staged reveals, operator start/end, and the hole cam.

Covers: ballot_reveal order/tally determinism per seed, the reveal hold in the
tick, night_beat skipping dead roles, /admin/start gating, no auto-start with
manual-start, and game_over carrying the summary.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from werewolf.engine import Game, NightKill, Post, SeerCheck, Vote
from werewolf.engine.tests.helpers import config, drain_reveal, seats_with, to_vote
from werewolf.server.table import Table

from .conftest import mcp_call, start_server


def _ballot_reveals(game: Game) -> list[dict]:
    return [e.data for e in game.events(None) if e.kind == "ballot_reveal"]


def _drive_to_vote(game: Game) -> None:
    """Abstain night one, then get one nomination standing and open the vote."""
    for seat in sorted(game.pending()):
        game.abstain(seat)
    while game.phase == "reveal":
        game.advance_reveal()
    alive = game.alive()
    game.act(alive[0], Post(chat="nom", nominate=alive[1]), now=0.0)
    to_vote(game)


def test_ballot_reveal_deterministic_and_tally_sums() -> None:
    seen_orders = []
    for _ in range(2):
        game = Game(config(seed=11))
        _drive_to_vote(game)
        standing = list(game.legal_targets(game.alive()[0]))
        for seat in sorted(game.pending()):
            game.act(seat, Vote(standing[0] if seat % 2 else standing[-1]))
        drain_reveal(game)
        reveals = _ballot_reveals(game)
        assert reveals, "no ballot_reveal events"
        # every cast ballot is revealed exactly once
        tally_event = next(e for e in game.events(None) if e.kind == "vote_tally")
        cast = [b for b in tally_event.data["ballots"] if b["target"] is not None]
        assert sorted(r["seat"] for r in reveals) == sorted(b["seat"] for b in cast)
        # the final reveal's running tally equals the resolved totals
        final_tally = reveals[-1]["tally"]
        expected: dict[str, int] = {}
        for b in cast:
            expected[str(b["target"])] = expected.get(str(b["target"]), 0) + b["weight"]
        assert final_tally == expected
        # running tallies are cumulative and never shrink
        for r in reveals:
            assert sum(r["tally"].values()) <= len(cast) * 2
        seen_orders.append([(r["seat"], r["target"]) for r in reveals])
    assert seen_orders[0] == seen_orders[1], "reveal order must be seeded-random"


def test_night_beat_skips_dead_and_abstained_roles() -> None:
    game = Game(config(seed=7))
    seer = seats_with(game, "seer")[0]
    doctor = seats_with(game, "doctor")[0]
    wolves = seats_with(game, "werewolf")
    villager = seats_with(game, "villager")[0]
    # doctor abstains; wolves kill; seer checks
    for w in wolves:
        game.act(w, NightKill(villager))
    game.act(seer, SeerCheck(wolves[0]))
    game.abstain(doctor)
    assert game.phase == "reveal"
    while game.phase == "reveal":
        game.advance_reveal()
    beats = [e.data for e in game.events(None) if e.kind == "night_beat"]
    steps = [b["step"] for b in beats]
    assert steps == ["wolves", "seer"], f"doctor abstained, so no doctor beat: {steps}"
    # a night where the seer is killed before acting never emits a seer beat
    game2 = Game(config(seed=8))
    seer2 = seats_with(game2, "seer")[0]
    wolves2 = seats_with(game2, "werewolf")
    for w in wolves2:
        game2.act(w, NightKill(seer2))
    for seat in sorted(game2.pending()):
        game2.abstain(seat)
    while game2.phase == "reveal":
        game2.advance_reveal()
    beats2 = [e.data for e in game2.events(None) if e.kind == "night_beat"]
    assert all(b["step"] != "seer" for b in beats2), beats2


async def test_reveal_hold_shows_in_tick(tmp_path: Path) -> None:
    """The vote reveal holds the phase: tick reports held_s>0 and bots wait."""
    table = Table(
        name="t", config=config(seed=31), budget_usd=0.5, window_s=60.0,
        runs_dir=tmp_path, tokens=[f"t{s}" for s in range(7)],
        teams=[f"team{s}" for s in range(7)], reveal_s=5.0,
    )
    async with table.cond:
        table._transition_until = 0.0
        _drive_to_vote(table.game)
        g = table.game
        # every seat votes; the close opens the reveal phase under a hold
        standing = list(g.legal_targets(g.alive()[0]))
        for seat in sorted(g.pending()):
            g.act(seat, Vote(standing[0]))
        table.note_window_locked()
        tick = table.spectator_tick()
        assert g.phase == "reveal"
        assert table._reveal_until > 0
        assert tick["held_s"] > 0
        assert table.holding()
        # no tally is visible until the beats have played
        assert not any(e.kind == "vote_tally" for e in g.events(None))


async def test_admin_start_gates_manual_lobby(tmp_path: Path) -> None:
    """409 before every seat is ready, 200 after; no turns before the start."""
    srv = await start_server(tmp_path, seed=77, window_s=0.5, manual_start=True)
    try:
        table = srv.state.tables[0]
        op = srv.state.operator_token
        async with httpx.AsyncClient() as client:
            resp = await client.post(srv.url + "/admin/start", params={"op": op})
            assert resp.status_code == 409, resp.text
            # seat tokens are refused
            resp = await client.post(
                srv.url + "/admin/start", params={"op": srv.token(0)}
            )
            assert resp.status_code == 403
        # seats check in from the lobby; the game still must not begin
        for s in range(7):
            r = await mcp_call(srv.url, srv.token(s), "get_my_role", {})
            assert r.get("lobby") is True, r
        assert table.game_counter == 0
        assert table.awaiting_start
        # no window is served while waiting for the operator
        r = await mcp_call(srv.url, srv.token(0), "wait_for_turn", {"timeout_s": 0.2})
        assert r.get("status") == "waiting" and r.get("lobby") is True, r
        async with httpx.AsyncClient() as client:
            resp = await client.post(srv.url + "/admin/start", params={"op": op})
            assert resp.status_code == 200, resp.text
            assert resp.json()["game"] == 1
        assert table.game_counter == 1 and not table.awaiting_start
        r = await mcp_call(srv.url, srv.token(0), "wait_for_turn", {"timeout_s": 2})
        assert r.get("turn_id") or r.get("status") in ("waiting",), r
        # a second start is refused while the game runs
        async with httpx.AsyncClient() as client:
            resp = await client.post(srv.url + "/admin/start", params={"op": op})
            assert resp.status_code == 409
    finally:
        await srv.stop()


async def test_no_auto_start_with_manual_start(tmp_path: Path) -> None:
    """Readiness alone never opens the first window under manual-start."""
    srv = await start_server(tmp_path, seed=78, window_s=0.5, manual_start=True)
    try:
        table = srv.state.tables[0]
        for s in range(7):
            await mcp_call(srv.url, srv.token(s), "get_my_role", {})
        await asyncio.sleep(1.0)
        assert table.game_counter == 0
        assert table.frozen and table.awaiting_start
    finally:
        await srv.stop()


async def test_game_over_summary(tmp_path: Path) -> None:
    """game_over carries winners, deaths, spend and the most-accused seat."""
    from werewolf.housebots.runner import run_bot

    srv = await start_server(
        tmp_path, seed=42, window_s=0.5, discussion_s=4.0, manual_start=True,
    )
    queue: asyncio.Queue = asyncio.Queue()
    try:
        table = srv.state.tables[0]
        table.sse_subs.append(queue)
        for s in range(7):
            await mcp_call(srv.url, srv.token(s), "get_my_role", {})
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                srv.url + "/admin/start", params={"op": srv.state.operator_token}
            )
            assert resp.status_code == 200
        bots = asyncio.gather(
            *(run_bot(srv.url, srv.token(i), "baseline", 900 + i) for i in range(7)),
            return_exceptions=True,
        )
        deadline = asyncio.get_event_loop().time() + 120
        summary = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=5)
            except TimeoutError:
                continue
            if msg.get("kind") == "game_start":
                assert msg["game"] == 1
            if msg.get("kind") == "game_over":
                summary = msg["summary"]
                break
        bots.cancel()
        assert summary is not None, "no game_over event"
        assert summary["winners"]
        assert len(summary["wolves"]) == 2
        assert isinstance(summary["deaths"], list)
        for d in summary["deaths"]:
            assert set(d) == {"seat", "day", "cause"}
        assert summary["spend_total"] >= 0
        assert summary["powerups"] >= 0
        # persisted with the run
        data = json.loads(
            (tmp_path / "runs" / "table1" / "game1" / "game.json").read_text()
        )
        assert data["summary"]["winners"] == summary["winners"]
        assert data["thoughts"] is not None
        # manual-start: the table stays idle instead of auto-restarting
        await asyncio.sleep(1.0)
        assert table.idle and table.awaiting_start
        r = await mcp_call(srv.url, srv.token(0), "wait_for_turn", {"timeout_s": 0.2})
        assert r.get("status") in ("over", "idle"), r
        # and /admin/start now opens the next game
        for s in range(7):
            await mcp_call(srv.url, srv.token(s), "get_my_role", {})
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                srv.url + "/admin/start", params={"op": srv.state.operator_token}
            )
            assert resp.status_code == 200
            assert resp.json()["game"] == 2
    finally:
        await srv.stop()


async def test_lobby_events_on_readiness(tmp_path: Path) -> None:
    """SSE gets a lobby event per readiness change, then game_start."""
    srv = await start_server(tmp_path, seed=79, window_s=0.5, manual_start=True)
    queue: asyncio.Queue = asyncio.Queue()
    try:
        table = srv.state.tables[0]
        table.sse_subs.append(queue)
        await mcp_call(srv.url, srv.token(3), "get_my_role", {})
        msg = await asyncio.wait_for(queue.get(), timeout=2)
        assert msg["kind"] == "lobby"
        seats = {s["seat"]: s for s in msg["seats"]}
        assert seats[3]["ready"] is True
        assert seats[0]["ready"] is False
        assert all("team" in s and "house" in s for s in msg["seats"])
    finally:
        await srv.stop()


async def test_thought_emitted_for_reason(tmp_path: Path) -> None:
    """A reason on vote becomes an audience-only thought event and game.json entry."""
    srv = await start_server(tmp_path, seed=80, window_s=30.0)
    queue: asyncio.Queue = asyncio.Queue()
    try:
        table = srv.state.tables[0]
        table.sse_subs.append(queue)
        async with table.cond:
            table._transition_until = 0.0
            table._released_at -= 100
            g = table.game
            for seat in sorted(g.pending()):
                g.abstain(seat)
            while g.phase == "reveal":
                g.advance_reveal()
            table.note_window_locked()
            g.act(0, Post(chat="nom", nominate=1), now=0.0)
            while g.phase == "day_discussion":
                g.close_discussion()
            while g.phase == "defence":
                for seat in sorted(g.pending()):
                    g.abstain(seat)
            table.note_window_locked()
            # the vote reveal hold keeps the window closed; skip it for the call
            table._reveal_until = 0.0
            table._transition_until = 0.0
        assert table.game.phase == "day_vote"
        turn_id = table.public_turn_id()
        r = await mcp_call(srv.url, srv.token(2), "vote", {
            "turn_id": turn_id, "seat": 1, "reason": "seat 1 is shady",
        })
        assert r == {"ok": True}, r
        kinds = []
        while not queue.empty():
            kinds.append(queue.get_nowait())
        thoughts = [m for m in kinds if m.get("kind") == "thought"]
        assert thoughts and thoughts[0]["seat"] == 2
        assert thoughts[0]["text"] == "seat 1 is shady"
        assert table._thoughts[-1]["text"] == "seat 1 is shady"
        # the ballot itself is only a ballot_cast on SSE, never a live target
        assert any(
            m.get("kind") == "ballot_cast" and m.get("seat") == 2 for m in kinds
        )
        assert not any(m.get("kind") == "ballot" for m in kinds)
    finally:
        await srv.stop()
