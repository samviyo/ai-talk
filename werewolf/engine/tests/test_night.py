"""Night resolution: plurality, seeded tie-break, zero ballots, doctor rules."""

from __future__ import annotations

import pytest

from werewolf.engine import DoctorSave, Game, IllegalAction, NightKill, SeerCheck
from werewolf.engine.tests.helpers import config, seats_with


def night_result(game: Game) -> dict:
    return next(e.data for e in game.events(None) if e.kind == "night_result")


def play_night(game: Game, kills: dict[int, int], save: int | None) -> None:
    for wolf in seats_with(game, "werewolf"):
        if wolf in kills:
            game.act(wolf, NightKill(kills[wolf]))
        else:
            game.abstain(wolf)
    seer = seats_with(game, "seer")[0]
    game.act(seer, SeerCheck(game.legal_targets(seer)[0]))
    doctor = seats_with(game, "doctor")[0]
    if save is None:
        game.abstain(doctor)
    else:
        game.act(doctor, DoctorSave(save))


def test_plurality_kill() -> None:
    game = Game(config(seed=7))
    wolves = seats_with(game, "werewolf")
    targets = game.legal_targets(wolves[0])
    play_night(game, {wolves[0]: targets[0], wolves[1]: targets[0]}, save=None)
    result = night_result(game)
    assert result["kill_target"] == targets[0]
    assert result["killed"] == targets[0]
    death = next(e for e in game.events(None) if e.kind == "death")
    assert death.data["seat"] == targets[0]
    assert death.data["cause"] == "night"
    assert death.data["role"] == game.role(targets[0])


def test_zero_ballots_no_kill() -> None:
    game = Game(config(seed=7))
    play_night(game, {}, save=None)
    result = night_result(game)
    assert result["kill_target"] is None
    assert result["killed"] is None
    assert any(e.kind == "no_death" and e.data["cause"] == "night" for e in game.events(None))


def test_tie_broken_by_seeded_rng() -> None:
    picks = []
    for seed in range(8):
        game = Game(config(seed=seed))
        wolves = seats_with(game, "werewolf")
        t0, t1 = game.legal_targets(wolves[0])[:2]
        play_night(game, {wolves[0]: t0, wolves[1]: t1}, save=None)
        picks.append(night_result(game)["kill_target"])
    assert len(set(picks)) > 1  # the tie-break actually varies with the seed
    # determinism: same seed, same tie -> same victim
    again = Game(config(seed=3))
    wolves = seats_with(again, "werewolf")
    t0, t1 = again.legal_targets(wolves[0])[:2]
    play_night(again, {wolves[0]: t0, wolves[1]: t1}, save=None)
    assert night_result(again)["kill_target"] == picks[3]


def test_doctor_save_blocks_kill() -> None:
    game = Game(config(seed=7))
    wolves = seats_with(game, "werewolf")
    target = game.legal_targets(wolves[0])[0]
    play_night(game, {w: target for w in wolves}, save=target)
    result = night_result(game)
    assert result["kill_target"] == target
    assert result["killed"] is None
    assert target in game.alive()


def test_doctor_may_save_self_but_not_repeat() -> None:
    game = Game(config(seed=7, rounds=0, max_days=5))
    doctor = seats_with(game, "doctor")[0]
    assert doctor in game.legal_targets(doctor)
    play_night(game, {}, save=doctor)
    while game.phase != "night":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    # night 2: last night's save target is off-limits
    assert doctor not in game.legal_targets(doctor)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(doctor, DoctorSave(doctor))


def test_doctor_repeat_restriction_expires_after_a_night() -> None:
    game = Game(config(seed=7, max_days=9))
    doctor = seats_with(game, "doctor")[0]
    wolves = seats_with(game, "werewolf")
    play_night(game, {}, save=doctor)
    # clear the rest of day 1 with abstentions to reach night 2
    while game.phase != "night":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    for wolf in wolves:
        game.abstain(wolf)
    seer = seats_with(game, "seer")[0]
    game.abstain(seer)
    game.act(doctor, DoctorSave(game.legal_targets(doctor)[0]))
    # night 3: doctor may save itself again
    while game.phase != "night":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    assert doctor in game.legal_targets(doctor)


def test_seer_result_private_and_correct() -> None:
    game = Game(config(seed=7))
    seer = seats_with(game, "seer")[0]
    wolf = seats_with(game, "werewolf")[0]
    game.act(seer, SeerCheck(wolf))
    result = next(
        e.data for e in game.events(seer) if e.kind == "seer_result"
    )
    assert result == {"seer": seer, "seat": wolf, "role": "werewolf"}
    others = [s for s in game.alive() if s != seer]
    assert all(
        not any(e.kind == "seer_result" for e in game.events(s)) for s in others
    )
