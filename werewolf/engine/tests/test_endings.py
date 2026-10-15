"""Win/draw conditions, determinism, config validation."""

from __future__ import annotations

import random

import pytest

from werewolf.engine import Config, Game, NightKill, SeerCheck, Vote
from werewolf.engine.sim import play, random_policy
from werewolf.engine.tests.helpers import config, seats_with, scripted


def test_config_rejects_too_few_seats() -> None:
    with pytest.raises(ValueError):
        Config(seats=6, wolves=2, discussion_rounds=1, max_days=5, seed=1)
    Config(seats=7, wolves=2, discussion_rounds=1, max_days=5, seed=1)  # boundary ok
    with pytest.raises(ValueError):
        Config(seats=10, wolves=4, discussion_rounds=1, max_days=5, seed=1)


def test_role_composition() -> None:
    game = Game(config(seats=7, wolves=2))
    roles = [game.role(s) for s in range(7)]
    assert roles.count("werewolf") == 2
    assert roles.count("seer") == 1
    assert roles.count("doctor") == 1
    assert roles.count("villager") == 3


def test_village_wins_when_wolves_dead() -> None:
    game = Game(config(seed=7, rounds=0))
    wolves = seats_with(game, "werewolf")
    # day 1: lynch wolf A; night: other wolf kills nobody (abstain); day 2: lynch wolf B
    for seat in sorted(game.pending()):
        game.abstain(seat)
    alive = game.alive()
    for seat in alive:
        game.act(seat, Vote(wolves[0]) if seat != wolves[0] else Vote(alive[-1]))
    assert game.role(wolves[0]) == "werewolf"
    assert wolves[0] not in game.alive()
    assert game.result is None
    for seat in sorted(game.pending()):
        game.abstain(seat)
    alive = game.alive()
    for seat in alive:
        game.act(seat, Vote(wolves[1]) if seat != wolves[1] else Vote(alive[0]))
    assert game.result == "village"
    assert game.phase == "over"
    assert game.events(None)[-1].kind == "result"


def test_wolves_win_on_parity() -> None:
    # 5 seats, 1 wolf: kill, lynch a townie, kill again -> 1 wolf vs 1 town is parity
    game = Game(config(seats=5, wolves=1, rounds=0, max_days=5, seed=7))
    wolf = seats_with(game, "werewolf")[0]
    town = [s for s in game.alive() if s != wolf]

    def clear_night(kill_target: int) -> None:
        game.act(wolf, NightKill(kill_target))
        for seat in sorted(game.pending()):
            game.abstain(seat)

    clear_night(town[0])
    alive = game.alive()
    lynch = next(s for s in alive if s != wolf)
    for seat in alive:
        game.act(seat, Vote(lynch) if seat != lynch else Vote(alive[-1] if alive[-1] != lynch else wolf))
    assert game.result is None
    clear_night(next(s for s in game.alive() if s != wolf))
    assert game.result == "wolves"


def test_max_days_draw() -> None:
    game = Game(config(seed=7, rounds=0, max_days=2))
    # everyone abstains forever: no kills, no eliminations -> draw when day 2's vote closes
    while game.result is None:
        for seat in sorted(game.pending()):
            game.abstain(seat)
    assert game.result == "draw"
    assert game.events(None)[-1].to_dict()["result"] == "draw"


def test_vote_tie_eliminates_nobody() -> None:
    game = Game(config(seats=7, wolves=2, rounds=0, max_days=3, seed=7))
    for seat in sorted(game.pending()):
        game.abstain(seat)
    alive = game.alive()
    a, b = alive[0], alive[1]
    voters = [s for s in alive[2:] if s != a and s != b]
    half = len(voters) // 2  # 7 seats: a,b + 5 voters -> 3v2 would decide, so split 2/2 + abstain
    game.act(a, Vote(b))
    game.act(b, Vote(a))
    for seat in voters[:half]:
        game.act(seat, Vote(a))
    for seat in voters[half : 2 * half]:
        game.act(seat, Vote(b))
    for seat in voters[2 * half :]:
        game.abstain(seat)
    tally = next(e for e in game.events(None) if e.kind == "vote_tally")
    assert tally.data["eliminated"] is None
    assert a in game.alive() and b in game.alive()
    assert any(e.kind == "no_death" and e.data["cause"] == "vote" for e in game.events(None))


def test_zero_ballots_eliminate_nobody() -> None:
    game = Game(config(seed=7, rounds=0))
    for seat in sorted(game.pending()):
        game.abstain(seat)
    for seat in sorted(game.pending()):
        game.abstain(seat)
    tally = next(e for e in game.events(None) if e.kind == "vote_tally")
    assert tally.data["eliminated"] is None
    assert all(b["target"] is None for b in tally.data["ballots"])


def test_determinism_same_seed_same_policy() -> None:
    cfg = config(seats=7, wolves=2, rounds=2, max_days=5, seed=42)
    a = play(cfg, random_policy(random.Random(99)))
    b = play(cfg, random_policy(random.Random(99)))
    assert [e.to_dict() for e in a.events(None)] == [e.to_dict() for e in b.events(None)]
    assert a.result == b.result


def test_event_to_dict_shape() -> None:
    game = Game(config(seed=7))
    for event in game.events(None):
        d = event.to_dict()
        assert set(d) >= {"kind", "day", "round"}
    assignment = next(e for e in game.events(None) if e.kind == "role_assignment")
    assert set(assignment.to_dict()) == {
        "kind", "day", "round", "seat", "role", "partners",
    }
