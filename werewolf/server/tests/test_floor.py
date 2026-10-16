"""The discussion floor: one voice at a time, replies, news, the hot seat."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .conftest import mcp_call, start_server
from .test_livechat import _advance_to_discussion

FLOOR = 0.3


async def _server(tmp_path: Path, hot_seat: bool = False, **kw):
    kw.setdefault("window_s", 60.0)
    kw.setdefault("discussion_s", 60.0)
    srv = await start_server(tmp_path, **kw)
    table = srv.state.tables[0]
    table.floor_s = FLOOR
    if hot_seat:
        table.hot_seat_s = 0.3
        table.hot_seat_answer_s = 0.4
    else:
        table.hot_seat_s = 9999.0  # disabled unless the test opts in
    return srv


async def _posts(srv, seat: int) -> list[dict]:
    for _ in range(10):
        r = await mcp_call(srv.url, srv.token(seat), "read_board", {"since": 0})
        if r.get("error") != "rate_limited":
            return r.get("posts") or []
        await asyncio.sleep(1.05)
    return []


async def _board(srv, seat: int) -> dict:
    for _ in range(10):
        r = await mcp_call(srv.url, srv.token(seat), "read_board", {"since": 0})
        if r.get("error") != "rate_limited":
            return r
        await asyncio.sleep(1.05)
    return {}


async def _force_release(table, now: float) -> None:
    async with table.cond:
        table._floor_hold = now
        table._speaking_seat = None
        table._pump_floor_locked()
        table._notify_sse_locked()
        table.cond.notify_all()


async def test_two_posts_release_floor_s_apart(tmp_path: Path):
    srv = await _server(tmp_path, seed=120)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        tid = table.public_turn_id()
        r0 = await mcp_call(srv.url, srv.token(0), "post",
                            {"turn_id": tid, "chat": "first voice"})
        assert r0.get("ok") is True and r0.get("queued") == 0, r0
        r1 = await mcp_call(srv.url, srv.token(1), "post",
                            {"turn_id": tid, "chat": "second voice"})
        assert r1.get("ok") is True and r1.get("queued") == 0, r1  # front index
        # seat 0's post is released at once; seat 1's is not on the board yet
        posts = await _posts(srv, 2)
        assert [p["seat"] for p in posts] == [0]
        await asyncio.sleep(FLOOR + 0.25)
        posts = await _posts(srv, 2)  # the 1s ticker releases the queue on time
        assert [p["seat"] for p in posts] == [0, 1], posts
    finally:
        await srv.stop()


async def test_second_post_from_one_seat_is_still_queued(tmp_path: Path):
    srv = await _server(tmp_path, seed=121)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        tid = table.public_turn_id()
        r0 = await mcp_call(srv.url, srv.token(0), "post",
                            {"turn_id": tid, "chat": "one"})
        assert r0.get("ok") is True, r0
        r1 = await mcp_call(srv.url, srv.token(0), "post",
                            {"turn_id": tid, "chat": "two"})
        assert r1.get("error") == "still_queued", r1
        # a still_queued rejection is not charged and not counted
        r2 = await mcp_call(srv.url, srv.token(0), "read_board", {"since": 0})
        assert r2.get("posts_left") == 2, r2
    finally:
        await srv.stop()


async def test_cut_off_refunds_queued_posts(tmp_path: Path):
    srv = await _server(tmp_path, seed=122, discussion_s=60.0)
    try:
        table = srv.state.tables[0]
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                srv.url + "/admin/house",
                params={"op": srv.state.operator_token},
                json={"1": "baseline"},
            )
            assert resp.status_code == 200
        await _advance_to_discussion(table)
        tid = table.public_turn_id()
        price = srv.state.house_prices["baseline"]
        r0 = await mcp_call(srv.url, srv.token(0), "post",
                            {"turn_id": tid, "chat": "from seat zero"})
        assert r0.get("ok") is True
        r1 = await mcp_call(srv.url, srv.token(1), "post",
                            {"turn_id": tid, "chat": "house post"})
        assert r1.get("ok") is True
        assert abs(table.seats[1].spent_usd - price) < 1e-9
        # the drain expires with seat 1 still queued: cut off and refunded
        async with table.cond:
            table.window_opened -= 120.0
            table._released_at -= 120.0
            table._transition_until = 0.0
            table._drain_deadline = time.monotonic() - 1.0
            table._floor_hold = time.monotonic() + 60.0  # still speaking
            await table.check_clock_locked()
            table.cond.notify_all()
        cut = next(e for e in table.game.events(None) if e.kind == "cut_off")
        assert cut.data["seats"] == [1]
        assert table.seats[1].spent_usd == 0.0, "cut post must be refunded"
        posts = await _posts(srv, 2)
        assert all(p["seat"] != 1 for p in posts)
    finally:
        await srv.stop()


async def test_reply_jumps_the_queue_once_per_accuser(tmp_path: Path):
    """Drive the floor directly under the lock: no wall-clock racing."""
    srv = await _server(tmp_path, seed=123)
    try:
        from werewolf.engine import Post

        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        g = table.game
        alive = g.alive()
        accuser, target, other, extra = alive[0], alive[1], alive[2], alive[3]

        def submit(seat, chat, **kw):
            return table.submit_discussion_post_locked(seat, Post(chat=chat, **kw), 0.0)

        def release():
            table._floor_hold = None
            table._speaking_seat = None
            table._pump_floor_locked()

        def drain():
            while table._floor_queue:
                release()

        async with table.cond:
            out = submit(accuser, f"seat {target} is a wolf", accuse=target)
            assert out.get("ok") is True and out.get("queued") == 0, out
            release()  # the accusation goes public
            assert g.reply_owed(target) is not None
            # the reply grant replaces the mention wake for that pair
            assert g.unread_mentions(target, 0) == []
            # target burns its three normal posts
            for i in range(3):
                out = submit(target, f"normal {i}")
                assert out.get("ok") is True, out
                release()
            assert g.posts_left(target) == 0
            out = submit(target, "one more")
            assert out.get("error") == "no_posts_left", out
            # the granted reply jumps a queued normal post, behind the speaker
            out = submit(other, "speaker holds the floor")
            assert out.get("ok") is True
            out = submit(extra, "queued behind the reply")
            assert out.get("ok") is True
            assert len(table._floor_queue) == 1  # the speaker is up, one queued
            out = submit(target, "my reply", reply_to=0)
            assert out.get("ok") is True and out.get("queued") == 0, out
            drain()
            assert g.posts_left(target) == 0, "a granted reply spends no post"
            assert g.reply_owed(target) is None
            # the released reply beat the queued normal post onto the board
            chats = [p["chat"] for p in g._posts]
            assert chats.index("my reply") < chats.index("queued behind the reply")
            # one grant per distinct accuser per day
            out = submit(accuser, f"still seat {target}", accuse=target)
            assert out.get("ok") is True, out
            drain()
            assert g.reply_owed(target) is None
    finally:
        await srv.stop()


async def test_wait_since_returns_news_and_plain_wait_does_not(tmp_path: Path):
    srv = await _server(tmp_path, seed=124)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        g = table.game
        tid = table.public_turn_id()
        # consume seat 1's opener wake so the news path is what fires
        first = await mcp_call(srv.url, srv.token(1), "wait_for_turn",
                               {"timeout_s": 2})
        assert first.get("you_owe") == "discuss", first
        cursor = len(g.events(1))
        await mcp_call(srv.url, srv.token(0), "post",
                       {"turn_id": tid, "chat": "something happened"})
        news = await mcp_call(srv.url, srv.token(1), "wait_for_turn",
                              {"timeout_s": 2, "since": cursor})
        assert news.get("status") == "news", news
        assert any(e["kind"] == "posts_revealed" for e in news["events"])
        assert news["cursor"] > cursor
        # a caught-up cursor yields no further news wake for seat 1
        idle = await mcp_call(srv.url, srv.token(1), "wait_for_turn",
                              {"timeout_s": 0.5, "since": news["cursor"]})
        assert idle.get("status") != "news", idle
        # without since, behaviour is unchanged: a normal discuss wake, no news
        plain = await mcp_call(srv.url, srv.token(1), "wait_for_turn",
                               {"timeout_s": 0.5})
        assert plain.get("status") != "news" and "events" not in plain, plain
    finally:
        await srv.stop()


async def test_reaction_limits_counts_and_silence(tmp_path: Path):
    srv = await _server(tmp_path, seed=125)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        g = table.game
        tid = table.public_turn_id()
        r = await mcp_call(srv.url, srv.token(0), "post",
                           {"turn_id": tid, "chat": "post zero"})
        assert r.get("ok") is True
        # once per seat per post
        r = await mcp_call(srv.url, srv.token(1), "react",
                           {"post_id": 0, "kind": "agree"})
        assert r.get("ok") is True, r
        r = await mcp_call(srv.url, srv.token(1), "react",
                           {"post_id": 0, "kind": "doubt"})
        assert r.get("error") == "already_acted", r
        # never on your own post
        r = await mcp_call(srv.url, srv.token(0), "react",
                           {"post_id": 0, "kind": "agree"})
        assert r.get("error") == "invalid_target", r
        # unknown kind
        r = await mcp_call(srv.url, srv.token(2), "react",
                           {"post_id": 0, "kind": "yikes"})
        assert r.get("error") == "bad_schema", r
        board = await _board(srv, 2)
        post = next(p for p in board["posts"] if p["id"] == 0)
        assert post["reactions"] == {"agree": 1}, post
        # 8 per seat per day: seat 2 reacts to two posts from each of four seats
        for round_ in range(2):
            for i, seat in enumerate([s for s in g.alive() if s not in (0, 2)]):
                r = await mcp_call(srv.url, srv.token(seat), "post",
                                   {"turn_id": tid, "chat": f"filler {round_}-{i}"})
                assert r.get("ok") is True, r
                async with table.cond:
                    table._floor_hold = None
                    table._speaking_seat = None
                    table._pump_floor_locked()
        ids = [p["id"] for p in (await _board(srv, 2))["posts"] if p["seat"] != 2]
        ok = 0
        for pid in ids:
            r = await mcp_call(srv.url, srv.token(2), "react",
                               {"post_id": pid, "kind": "doubt"})
            if r.get("ok"):
                ok += 1
        assert ok == 8, (ok, ids)
        # the 9th reaction in a window hits the shared 10-call budget first
        r = await mcp_call(srv.url, srv.token(2), "react",
                           {"post_id": ids[0], "kind": "suspect"})
        assert r.get("error") in ("rate_limited", "already_acted", "call_budget_exceeded"), r
        # a reaction clears a seat from the silent list: compare against no reactions
        assert 2 not in g._silent_seats()
        g._reactions_today.pop(2, None)
        assert 2 in g._silent_seats()  # without the reactions seat 2 was silent
    finally:
        await srv.stop()


async def test_hot_seat_picks_the_quietest_seat_deterministically(tmp_path: Path):
    srv = await _server(tmp_path, seed=126, hot_seat=True)
    try:
        table = srv.state.tables[0]
        await _advance_to_discussion(table)
        g = table.game
        g._posts_today[g.alive()[0]] = 1  # everyone else is quieter
        async with table.cond:
            table._dead_air_since -= table.hot_seat_s + 1.0
            await table.check_clock_locked()
        assert table._hot_active is not None
        seat = table._hot_active["seat"]
        assert seat != g.alive()[0]
        # the same seed picks the same seat on a fresh game
        srv2 = await _server(tmp_path / "b", seed=126, hot_seat=True)
        try:
            t2 = srv2.state.tables[0]
            await _advance_to_discussion(t2)
            t2.game._posts_today[t2.game.alive()[0]] = 1
            async with t2.cond:
                t2._dead_air_since -= t2.hot_seat_s + 1.0
                await t2.check_clock_locked()
            assert t2._hot_active["seat"] == seat
        finally:
            await srv2.stop()
        # the pick lands as you_owe on wait_for_turn
        turn = await mcp_call(srv.url, srv.token(seat), "wait_for_turn",
                              {"timeout_s": 2})
        assert turn.get("you_owe") == "hot_seat" and turn.get("prompt"), turn
    finally:
        await srv.stop()


async def test_hot_seat_timeout_fines_and_can_break(tmp_path: Path):
    """A miss charges hot_seat_fine x the budget through the spend ledger."""
    srv = await _server(tmp_path, seed=127, budget=0.05, hot_seat=True)
    try:
        table = srv.state.tables[0]
        table.hot_seat_answer_s = 30.0  # generous; the test sets the deadline
        await _advance_to_discussion(table)
        async with table.cond:
            table._dead_air_since -= table.hot_seat_s + 1.0
            await table.check_clock_locked()
        hot = table._hot_active["seat"]
        async with table.cond:
            table._hot_active["deadline"] = time.monotonic() - 0.01
            await table.check_clock_locked()
        fine = round(table.hot_seat_fine_frac * table.budget_usd, 6)
        assert table.seats[hot].spent_usd == fine
        assert not table.is_broke(hot)
        passes = [e for e in table.game.events(None) if e.kind == "hot_seat_pass"]
        assert not passes  # public only when the day window closes
    finally:
        await srv.stop()


async def test_hot_seat_timeout_can_break_a_seat(tmp_path: Path):
    """The fine lands on the same ledger, so it can trigger broke."""
    srv = await _server(tmp_path, seed=131, budget=0.05, hot_seat=True)
    try:
        table = srv.state.tables[0]
        table.hot_seat_answer_s = 30.0
        await _advance_to_discussion(table)
        async with table.cond:
            table._dead_air_since -= table.hot_seat_s + 1.0
            await table.check_clock_locked()
        hot = table._hot_active["seat"]
        fine = round(table.hot_seat_fine_frac * table.budget_usd, 6)
        async with table.cond:
            table.seats[hot].spent_usd = table.budget_usd - fine  # one miss from broke
            table._hot_active["deadline"] = time.monotonic() - 0.01
            await table.check_clock_locked()
        assert table.is_broke(hot), "the fine should flip the seat to broke"
        r = await mcp_call(srv.url, srv.token(hot), "get_budget", {})
        assert r.get("broke") is True
    finally:
        await srv.stop()


async def test_late_hot_seat_post_is_fined_and_counts(tmp_path: Path):
    srv = await _server(tmp_path, seed=129, hot_seat=True)
    try:
        table = srv.state.tables[0]
        table.hot_seat_answer_s = 30.0
        await _advance_to_discussion(table)
        g = table.game
        async with table.cond:
            table._dead_air_since -= table.hot_seat_s + 1.0
            await table.check_clock_locked()
        hot = table._hot_active["seat"]
        async with table.cond:
            table._hot_active["deadline"] = time.monotonic() + 0.05
        await asyncio.sleep(0.3)  # the ticker passes the seat
        tid = table.public_turn_id()
        before = g._posts_today.get(hot, 0)
        r = await mcp_call(srv.url, srv.token(hot), "post",
                           {"turn_id": tid, "chat": "too slow"})
        assert r.get("ok") is True, r
        assert table._hot_active is None
        assert table.seats[hot].spent_usd == table.hot_seat_fine_frac * table.budget_usd
        assert g._posts_today.get(hot, 0) == before + 1, "a late answer counts"
    finally:
        await srv.stop()


async def test_in_time_hot_seat_answer_is_free_and_jumps(tmp_path: Path):
    srv = await _server(tmp_path, seed=130, hot_seat=True)
    try:
        table = srv.state.tables[0]
        table.hot_seat_answer_s = 30.0
        await _advance_to_discussion(table)
        g = table.game
        async with table.cond:
            table._dead_air_since -= table.hot_seat_s + 1.0
            await table.check_clock_locked()
        hot = table._hot_active["seat"]
        tid = table.public_turn_id()
        before = g._posts_today.get(hot, 0)
        r = await mcp_call(srv.url, srv.token(hot), "post",
                           {"turn_id": tid, "chat": "hot answer"})
        assert r.get("ok") is True, r
        assert g._posts_today.get(hot, 0) == before, "hot-seat answers don't count"
        assert table.seats[hot].spent_usd == 0.0
        assert table._hot_active is None  # a released hot answer clears the seat
    finally:
        await srv.stop()
