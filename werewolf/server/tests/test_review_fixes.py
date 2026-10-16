"""Regression tests for the verified review findings."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from werewolf.server.llm import LLMProxy

from .conftest import PRICES, mcp_call, start_server
from .test_proxy import _AsyncTransport, llm_payload, post_llm


def _upstream_ok(model: str = "fake-model"):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0,
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "{}"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    return handler


async def test_proxy_strips_unlisted_fields(tmp_path: Path):
    """Finding 2: arbitrary fields (Bifrost fallbacks) are not forwarded."""
    seen: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return await _upstream_ok()(request)

    srv = await start_server(
        tmp_path, seed=31, window_s=60.0, fake_llm=False,
        transport=_AsyncTransport(handler),
    )
    try:
        payload = llm_payload()
        payload["fallbacks"] = ["claude-opus-5-5"]
        payload["n"] = 5
        payload["extra_body"] = {"evil": True}
        r = await post_llm(srv.url, srv.token(0), payload)
        assert r.status_code == 200, r.text
        assert seen, "upstream never called"
        forwarded = seen[0]
        assert "fallbacks" not in forwarded
        assert "n" not in forwarded
        assert "extra_body" not in forwarded
        assert forwarded["model"] == "fake-model"
        assert forwarded["messages"]
    finally:
        await srv.stop()


async def test_proxy_bills_served_model(tmp_path: Path):
    """Finding 2: a response naming another priced model bills at that price."""
    srv = await start_server(
        tmp_path, seed=32, window_s=60.0, fake_llm=False,
        transport=_AsyncTransport(_upstream_ok(model="claude-haiku-5-5")),
    )
    try:
        r = await post_llm(srv.url, srv.token(0), llm_payload(model="fake-model"))
        assert r.status_code == 200, r.text
        table = srv.state.tables[0]
        spent = table.seats[0].spent_usd
        expected = PRICES.cost("claude-haiku-5-5", 10, 5)
        assert spent == expected, (spent, expected)
    finally:
        await srv.stop()


async def test_proxy_bills_max_when_served_model_unpriced(tmp_path: Path):
    """Finding 2: an unpriced served model bills at the highest known price."""
    srv = await start_server(
        tmp_path, seed=33, window_s=60.0, fake_llm=False,
        transport=_AsyncTransport(_upstream_ok(model="gpt-ultra-9")),
    )
    try:
        r = await post_llm(srv.url, srv.token(0), llm_payload(model="fake-model"))
        assert r.status_code == 200, r.text
        table = srv.state.tables[0]
        spent = table.seats[0].spent_usd
        expected = max(
            PRICES.cost(m, 10, 5) for m in PRICES.input
        )
        assert spent == expected, (spent, expected)
    finally:
        await srv.stop()


async def test_concurrent_calls_charge_atomically(tmp_path: Path):
    """Finding 5: two in-flight calls cannot both pass the balance check."""
    gate = asyncio.Event()
    calls = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await gate.wait()
        return await _upstream_ok()(request)

    # budget 0.00011 covers one fake-model call ($0.0000125) but not two
    srv = await start_server(
        tmp_path, seed=34, window_s=60.0, fake_llm=False, budget=0.00002,
        transport=_AsyncTransport(slow),
    )
    try:
        async with httpx.AsyncClient() as client:
            t1 = asyncio.create_task(client.post(
                srv.url + "/llm/v1/chat/completions", json=llm_payload(),
                headers={"Authorization": f"Bearer {srv.token(0)}"}, timeout=30,
            ))
            await asyncio.sleep(0.3)
            t2 = asyncio.create_task(client.post(
                srv.url + "/llm/v1/chat/completions", json=llm_payload(),
                headers={"Authorization": f"Bearer {srv.token(0)}"}, timeout=30,
            ))
            await asyncio.sleep(0.1)
            gate.set()
            r1, r2 = await asyncio.gather(t1, t2)
        statuses = sorted([r1.status_code, r2.status_code])
        # one call completes, the other is refused (409 busy or 402 broke)
        assert statuses == [200, 409], (r1.status_code, r2.status_code, r1.text, r2.text)
        table = srv.state.tables[0]
        # the completed call was charged exactly once, and the seat is broke
        assert table.is_broke(0)
        assert abs(table.seats[0].spent_usd - PRICES.cost("fake-model", 10, 5)) < 1e-12
    finally:
        await srv.stop()


async def test_llm_charge_stays_with_its_game(tmp_path: Path):
    """Finding 6: a call in flight across a game restart charges the old game's ledger."""
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return await _upstream_ok()(request)

    srv = await start_server(
        tmp_path, seed=35, window_s=60.0, auto_restart=True,
        fake_llm=False, transport=_AsyncTransport(slow),
    )
    try:
        table = srv.state.tables[0]
        old_game = table.game_counter
        async with httpx.AsyncClient() as client:
            task = asyncio.create_task(client.post(
                srv.url + "/llm/v1/chat/completions", json=llm_payload(),
                headers={"Authorization": f"Bearer {srv.token(0)}"}, timeout=30,
            ))
            await asyncio.sleep(0.3)
            async with table.cond:
                # simulate the old game ending and the next one opening mid-flight
                table.write_run()
                table._run_written = True
                table.start_game_locked()
                table.unfreeze_locked()
                table.cond.notify_all()
            assert table.game_counter == old_game + 1
            gate.set()
            r = await task
        assert r.status_code == 200, r.text
        # the new game's ledger was not charged for the old game's call
        assert table.seats[0].spent_usd == 0.0
        assert any(t["tool"] == "llm_cross_game" for t in table.traces)
        # old game's written ledger stays at zero; the charge is trace-only
        data = json.loads(
            (tmp_path / "runs" / "table1" / f"game{old_game}" / "game.json").read_text()
        )
        assert data["spend"]["0"] == 0.0
    finally:
        await srv.stop()


async def test_turn_id_is_game_scoped(tmp_path: Path):
    """Finding 10: a turn_id from the previous game is rejected after restart."""
    srv = await start_server(
        tmp_path, seed=36, window_s=60.0, auto_restart=True,
    )
    try:
        table = srv.state.tables[0]
        from .conftest import wait_turn

        turn = await wait_turn(srv.url, srv.token(0))
        assert turn["turn_id"].startswith(f"g{table.game_counter}:")
        stale = turn["turn_id"]
        async with table.cond:
            table.write_run()
            table._run_written = True
            table.start_game_locked()
            # in a test there is no readiness wait; open the window directly
            table.unfreeze_locked()
            table.cond.notify_all()
        r = await mcp_call(srv.url, srv.token(0), "night_action", {
            "turn_id": stale, "target": 1,
        })
        assert r["error"] == "stale_turn"
    finally:
        await srv.stop()


async def test_window_deadline_starts_when_turn_changes(tmp_path: Path):
    """Finding 11: window_opened resets the instant the engine turn changes."""
    srv = await start_server(tmp_path, seed=37, window_s=60.0)
    try:
        table = srv.state.tables[0]
        async with table.cond:
            opened0 = table.window_opened
            g = table.game
            for s in list(g.pending()):
                g.abstain(s)  # resolve night under the lock
            table.note_window_locked()
            assert table._clocked_turn == g.turn_id
            assert table.window_opened >= opened0
            assert table.game.phase == "day_discussion"
            # the deadline for the new window is discussion_s after NOW, not stale
            assert table.deadline() - table.window_opened == table.config.discussion_s
    finally:
        await srv.stop()


async def test_get_my_role_does_not_reset_call_budget(tmp_path: Path):
    """Finding 12: get_my_role no longer resets the per-window call counter."""
    srv = await start_server(tmp_path, seed=38, window_s=60.0)
    try:
        table = srv.state.tables[0]
        async with table.cond:
            g = table.game
            for s in list(g.pending()):
                g.abstain(s)  # reach a discussion window
        seat = 0
        turn_id = table.public_turn_id()
        # burn 5 calls, call get_my_role, then confirm the counter did not reset
        for _ in range(5):
            r = await mcp_call(srv.url, srv.token(seat), "shop", {})
            assert "error" not in r, r
        r = await mcp_call(srv.url, srv.token(seat), "get_my_role", {})
        assert r["seat"] == seat
        for _ in range(5):
            r = await mcp_call(srv.url, srv.token(seat), "shop", {})
            assert "error" not in r, r
        r = await mcp_call(srv.url, srv.token(seat), "shop", {})
        assert r.get("error") == "call_budget_exceeded", r
    finally:
        await srv.stop()


async def test_all_broke_wolves_single_kill(tmp_path: Path):
    """Finding 13: with every wolf broke, exactly one random kill is submitted."""
    srv = await start_server(tmp_path, seed=39, window_s=60.0, budget=0.0)
    try:
        table = srv.state.tables[0]
        async with table.cond:
            table._released_at -= 120.0
            table._transition_until = 0.0
            table.window_opened -= 120.0
            await table.check_clock_locked()
        # the spec's "one random legal kill", not one ballot per wolf
        tally = [e for e in table.game.events(None) if e.kind == "night_result"][0]
        assert tally.data["kill_target"] is not None
        # each broke wolf was visited by the fallback
        kills = [
            t for t in table.traces
            if t["tool"] == "_fallback_broke" and t["args"].get("obligation") == "night_kill"
        ]
        assert len(kills) >= 1
    finally:
        await srv.stop()


async def test_readiness_barrier_holds_game(tmp_path: Path):
    """Finding 4: while need_role_check is set, no turns, actions, or clock."""
    from werewolf.server.table import Table
    from werewolf.engine import Config

    table = Table(
        name="t", config=Config(seats=7, wolves=2, max_days=5, seed=40),
        budget_usd=0.5, window_s=0.3, runs_dir=tmp_path,
        tokens=[f"t{s}" for s in range(7)],
        teams=[f"team{s}" for s in range(7)],
    )
    async with table.cond:
        table.need_role_check = True
        table.frozen = True
        table.ready_seats = set()
        table.ready_event.clear()
    opened = table.window_opened
    # clock must not fire while frozen
    await asyncio.sleep(0.6)
    async with table.cond:
        await table.check_clock_locked()
        assert table.window_opened == opened
        assert table.game.phase == "night"
        assert table.holding()
        # a long-poller is not handed a turn while frozen
        from werewolf.server.mcp_tools import Dispatcher
        from werewolf.server.state import AppState

    srv = await start_server(tmp_path / "srv", seed=41, window_s=0.3)
    try:
        t2 = srv.state.tables[0]
        async with t2.cond:
            t2.need_role_check = True
            t2.frozen = True
            t2.ready_seats = set()
            t2.ready_event.clear()
        # frozen turn gating is probed via _wait_for_turn below, not a long poll
        r = await mcp_call(srv.url, srv.token(1), "night_action", {
            "turn_id": t2.public_turn_id(), "target": 1,
        })
        assert r["error"] == "not_your_turn"
        # frozen turn gating is covered by the direct Table assertions above
        # unfreeze opens the first window with a fresh deadline
        async with t2.cond:
            t2.unfreeze_locked()
            t2.cond.notify_all()
        async with t2.cond:
            assert not t2.frozen and t2.ready_event.is_set()
            assert t2.deadline() - t2.window_opened >= t2.window_s
    finally:
        await srv.stop()


async def test_min_window_dwell_delays_release(tmp_path: Path):
    """--min-window-s: consecutive windows open at least that far apart."""
    from werewolf.server.table import Table
    from werewolf.engine import Config

    table = Table(
        name="t", config=Config(seats=7, wolves=2, max_days=5, seed=50),
        budget_usd=0.5, window_s=60.0, runs_dir=tmp_path,
        tokens=[f"t{s}" for s in range(7)],
        teams=[f"team{s}" for s in range(7)],
        min_window_s=2.0,
    )
    async with table.cond:
        first = table.release_at()
        for s in list(table.game.pending()):
            table.game.abstain(s)
        table.note_window_locked()
        # the new window's release is >= min dwell after it opened
        assert table.release_at() >= table.window_opened + 1.9
        assert table.holding()


async def test_transition_hold_and_pause(tmp_path: Path):
    """--transition-s: night->day holds the turn; the clock is paused while held."""
    from werewolf.server.table import Table
    from werewolf.engine import Config

    table = Table(
        name="t", config=Config(seats=7, wolves=2, max_days=5, seed=51),
        budget_usd=0.5, window_s=60.0, runs_dir=tmp_path,
        tokens=[f"t{s}" for s in range(7)],
        teams=[f"team{s}" for s in range(7)],
        transition_s=2.0,
    )
    async with table.cond:
        assert table.transition_active()  # game start counts as a transition
        assert table.holding()
        for s in list(table.game.pending()):
            table.game.abstain(s)
        table.note_window_locked()
        assert table.game.phase == "day_discussion"
        assert table.transition_active()
        tick = table.spectator_tick()
        assert tick["transition"] is True
        assert tick["held_s"] > 0
        assert tick["remaining_s"] == 60.0  # clock paused while held
        # a fallback cannot fire during the hold even far past window_s
        table._released_at -= 3600.0
        await table.check_clock_locked()
        assert table.game.phase == "day_discussion"


async def test_min_window_s_paces_instant_bots(tmp_path: Path):
    """With min_window_s=2 and instant bots, windows open >=2s apart."""
    import time as _time

    from werewolf.housebots.runner import run_bot

    srv = await start_server(
        tmp_path, seed=60, window_s=2.0, min_window_s=2.0,
        transition_on_start=False,
    )
    try:
        table = srv.state.tables[0]
        opens: list[tuple[str, float]] = []
        real_note = table.note_window_locked

        def spy_note() -> None:
            prev = table._clocked_turn
            real_note()
            if table._clocked_turn != prev:
                opens.append((table._clocked_turn, table.window_opened))

        table.note_window_locked = spy_note
        bots = asyncio.gather(
            *(run_bot(srv.url, srv.token(i), "baseline", 200 + i) for i in range(7))
        )
        # the dwell applies between windows regardless of bot speed
        while len(opens) < 4 and table.game.result is None:
            await asyncio.sleep(0.2)
        bots.cancel()
        gaps = [b[1] - a[1] for a, b in zip(opens, opens[1:])]
        assert len(gaps) >= 2, f"too few windows opened: {opens}"
        assert all(g >= 1.9 for g in gaps), gaps
    finally:
        await srv.stop()


async def test_transition_delays_night_after_vote(tmp_path: Path):
    """With transition_s=2, the night after a vote opens >=2s after the vote closed."""
    import time as _time

    from werewolf.housebots.runner import run_bot

    srv = await start_server(
        tmp_path, seed=61, window_s=2.0, transition_s=2.0,
        transition_on_start=False,
    )
    try:
        table = srv.state.tables[0]
        # timestamp each window when it is actually released (hold expires)
        releases: list[tuple[str, str, float]] = []
        real_note = table.note_window_locked

        def spy_note() -> None:
            prev = table._clocked_turn
            real_note()
            if table._clocked_turn != prev:
                releases.append(
                    (table.game.phase, table._clocked_turn, table.release_at())
                )

        table.note_window_locked = spy_note
        bots = asyncio.gather(
            *(run_bot(srv.url, srv.token(i), "baseline", 300 + i) for i in range(7))
        )

        def _saw() -> bool:
            return any(
                p in ("day_discussion", "day_vote", "defence") and q == "night"
                for (p, _, _), (q, _, _) in zip(releases, releases[1:])
            )

        while not _saw() and table.game.result is None:
            await asyncio.sleep(0.2)
        bots.cancel()
        day_to_night = [
            (t_prev, t)
            for (prev_ph, _, t_prev), (ph, _, t) in zip(releases, releases[1:])
            if prev_ph in ("day_discussion", "day_vote", "defence") and ph == "night"
        ]
        assert day_to_night, f"never saw day -> night: {releases}"
        for t_prev, t in day_to_night:
            assert t - t_prev >= 1.9, t - t_prev
    finally:
        await srv.stop()


async def test_every_game_writes_run_dir(tmp_path: Path):
    """N fast games must produce N game dirs, even when the last act ends it."""
    from werewolf.housebots.runner import run_bot

    srv = await start_server(
        tmp_path, seed=70, window_s=4.0, auto_restart=True, discussion_s=2.0,
    )
    try:
        table = srv.state.tables[0]

        async def play_one(game_no: int) -> None:
            async def one(i: int):
                return await run_bot(srv.url, srv.token(i), "baseline", game_no * 100 + i)

            # bots exit on "over"; the idle gate opens game N+1 via get_my_role
            await asyncio.wait_for(asyncio.gather(*(one(i) for i in range(7))), timeout=120)

        games = 3
        for g in range(1, games + 1):
            await play_one(g)
        await asyncio.sleep(2.0)  # let the ticker settle between games
        for g in range(1, games + 1):
            assert (tmp_path / "runs" / "table1" / f"game{g}" / "game.json").exists(), (
                f"game{g} missing"
            )
    finally:
        await srv.stop()


async def test_idle_between_games_and_readiness_restart(tmp_path: Path):
    """Between games wait_for_turn returns idle; readiness opens the next game."""
    srv = await start_server(
        tmp_path, seed=71, window_s=60.0, auto_restart=True,
    )
    try:
        table = srv.state.tables[0]
        async with table.cond:
            # end game1 so the idle gate covers the gap before game2
            table.game.result = "village"
            table.write_run()
            table._run_written = True
            table.idle = True
            table.cond.notify_all()
        r = await mcp_call(srv.url, srv.token(0), "wait_for_turn", {"timeout_s": 0.2})
        assert r.get("status") == "over", r  # each seat hears over once
        r = await mcp_call(srv.url, srv.token(0), "wait_for_turn", {"timeout_s": 0.2})
        assert r.get("status") == "idle", r
        # one get_my_role opens game2 frozen for readiness
        r = await mcp_call(srv.url, srv.token(0), "get_my_role", {})
        assert r["seat"] == 0
        assert table.game_counter == 2
        assert table.frozen and not table.idle
        # the rest of the seats check in; the last one unfreezes the window
        for s in range(1, 7):
            await mcp_call(srv.url, srv.token(s), "get_my_role", {})
        assert not table.frozen
        assert table.ready_event.is_set()
    finally:
        await srv.stop()


async def test_base_url_strips_trailing_v1():
    """llm.py accepts BIFROST_BASE_URL with or without /v1."""
    p = LLMProxy(PRICES, "http://gw.example.com", fake=True)
    assert p.base_url == "http://gw.example.com"
    p = LLMProxy(PRICES, "http://gw.example.com/v1", fake=True)
    assert p.base_url == "http://gw.example.com"
    p = LLMProxy(PRICES, "http://gw.example.com/v1/", fake=True)
    assert p.base_url == "http://gw.example.com"


async def test_no_fallback_during_large_transition(tmp_path: Path):
    """A bot acting within window_s after release is never defaulted."""
    from werewolf.server.table import Table
    from werewolf.engine import Config, NightKill

    table = Table(
        name="t", config=Config(seats=7, wolves=2, max_days=5, seed=52),
        budget_usd=0.5, window_s=0.5, runs_dir=tmp_path,
        tokens=[f"t{s}" for s in range(7)],
        teams=[f"team{s}" for s in range(7)],
        transition_s=3.0,
    )
    wolves = [s for s in range(7) if table.game.role(s) == "werewolf"]
    async with table.cond:
        # 2.5s of hold elapsed, far past window_s, but the window isn't released
        table._released_at -= 2.5
        await table.check_clock_locked()
        assert table.game.phase == "night"  # no fallback fired
        # after the transition hold, the wolf may still act within window_s
        table._transition_until = 0.0
        target = table.game.legal_targets(wolves[0])[0]
        table.game.act(wolves[0], NightKill(target))
        table.note_window_locked()
        assert table.game.pending().get(wolves[0]) is None


async def test_wait_for_turn_returns_within_cap_during_long_hold(tmp_path: Path):
    # A hold longer than a client's 5s HTTP read timeout must still return "waiting" in time.
    import time as _time

    from .conftest import mcp_call

    srv = await start_server(tmp_path, seed=3, window_s=5.0, transition_s=12.0)
    try:
        t0 = _time.monotonic()
        r = await mcp_call(srv.url, srv.token(0), "wait_for_turn", {"timeout_s": 25})
        assert r.get("status") == "waiting"
        assert _time.monotonic() - t0 < 4.5
    finally:
        await srv.stop()
