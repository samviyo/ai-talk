"""Live-chat rules: nominations, seconds, standing, defences, seeded ties."""

from __future__ import annotations

import pytest

from werewolf.engine import Game, IllegalAction, Post
from werewolf.engine.tests.helpers import config, end_discussion, end_night


def _day(game: Game) -> list[int]:
    end_night(game)
    return game.alive()


def test_nominate_once_per_day_and_second_rules() -> None:
    game = Game(config(seed=7))
    a, b, c, d = _day(game)[:4]
    game.act(a, Post(chat=f"seat {b} sus", nominate=b), now=0.0)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "already_acted"):
        game.act(a, Post(chat=f"and seat {c}", nominate=c), now=6.0)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(a, Post(chat="self second", second=b), now=6.0)  # own nomination
    game.act(c, Post(chat="seconded", second=b), now=0.0)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "already_acted"):
        game.act(c, Post(chat="again", second=b), now=6.0)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(d, Post(chat="second nobody", second=99), now=0.0)


def test_nomination_and_seconded_events() -> None:
    game = Game(config(seed=7))
    a, b, c = _day(game)[:3]
    game.act(a, Post(chat=f"seat {b}", nominate=b), now=0.0)
    game.act(c, Post(chat="yes", second=b), now=0.0)
    kinds = [e.kind for e in game.events(None)]
    assert "nomination" in kinds and "seconded" in kinds
    nom = next(e for e in game.events(None) if e.kind == "nomination")
    assert nom.data == {"seat": a, "target": b}


def test_standing_picks_most_seconded_with_seeded_ties() -> None:
    # four-way tie on one second each: 3 stand, chosen by the seed
    standings = []
    for seed in range(16):
        game = Game(config(seats=12, wolves=3, seed=seed))
        alive = _day(game)
        for i in range(4):
            n, t, s = alive[i], alive[8 + i], alive[4 + i]
            game.act(n, Post(chat=f"seat {t}", nominate=t), now=0.0)
            game.act(s, Post(chat="second", second=t), now=0.0)
        end_discussion(game)
        standing = next(e for e in game.events(None) if e.kind == "standing")
        assert len(standing.data["targets"]) == 3
        assert set(standing.data["targets"]) <= set(alive[8:12])
        standings.append(tuple(standing.data["targets"]))
    assert len(set(standings)) > 1  # the tie-break varies with the seed


def test_later_support_displaces_early_nomination() -> None:
    game = Game(config(seed=7))
    alive = _day(game)
    a, b, c = alive[0], alive[1], alive[2]
    # Four nominees: b has zero seconds, c/d/e get one each; b drops off.
    d, e = alive[3], alive[4]
    f, g, h = alive[5], alive[6], alive[0]
    game.act(a, Post(chat=f"seat {b}", nominate=b), now=0.0)  # zero seconds
    game.act(b, Post(chat=f"seat {c}", nominate=c), now=0.0)
    game.act(f, Post(chat="second", second=c), now=0.0)
    game.act(c, Post(chat=f"seat {d}", nominate=d), now=0.0)
    game.act(g, Post(chat="second", second=d), now=0.0)
    game.act(d, Post(chat=f"seat {e}", nominate=e), now=0.0)
    game.act(h, Post(chat="second", second=e), now=6.0)
    end_discussion(game)
    standing = next(e2 for e2 in game.events(None) if e2.kind == "standing")
    assert standing.data["targets"] == [c, d, e]


def test_defence_window_opens_for_standing_nominee() -> None:
    game = Game(config(seed=7))
    alive = _day(game)
    a, b = alive[0], alive[1]
    game.act(a, Post(chat=f"seat {b}", nominate=b), now=0.0)
    end_discussion(game)
    assert game.phase == "defence"
    assert game.pending() == {b: "defence"}
    assert f"defence:{b}" in game.turn_id
    phase = next(
        e for e in reversed(game.events(None))
        if e.kind == "phase" and e.data["phase"] == "defence"
    )
    assert phase.data["seat"] == b
    # only the nominee may post, and exactly once, outside max_posts
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_your_turn"):
        game.act(a, Post(chat="not my defence"))
    game.act(b, Post(chat="I am innocent, I swear"))
    assert game.phase == "day_vote"


def test_defence_times_out_to_the_vote() -> None:
    game = Game(config(seed=7))
    alive = _day(game)
    a, b, c = alive[0], alive[1], alive[2]
    game.act(a, Post(chat=f"seat {b}", nominate=b), now=0.0)
    end_discussion(game)
    while game.phase == "defence":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    assert game.phase == "day_vote"
    assert game.legal_targets(a) == [b]


def test_board_state_tracks_nominations_and_posts_left() -> None:
    game = Game(config(seed=7))
    alive = _day(game)
    a, b, c = alive[0], alive[1], alive[2]
    game.act(a, Post(chat=f"seat {b}", nominate=b), now=0.0)
    game.act(c, Post(chat="second", second=b), now=0.0)
    state = game.board_state(a, since=0)
    assert len(state["posts"]) == 2
    assert state["nominations"] == [{"target": b, "by": a, "seconds": [c]}]
    assert state["posts_left"] == game.config.max_posts - 1
    end_discussion(game)
    state = game.board_state(a, since=0)
    assert state["standing"] == [b]
    assert state["posts_left"] == 0  # discussion closed


def test_silent_list_on_vote_tally() -> None:
    game = Game(config(seed=7))
    alive = _day(game)
    a, b = alive[0], alive[1]
    game.act(a, Post(chat=f"seat {b}", nominate=b), now=0.0)
    end_discussion(game)
    while game.phase == "defence":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    for seat in sorted(game.pending()):
        game.abstain(seat)
    tally = next(e for e in game.events(None) if e.kind == "vote_tally")
    expected_silent = [s for s in alive if s != a]
    assert tally.data["silent"] == expected_silent
