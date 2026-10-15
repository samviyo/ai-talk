"""Window/obligation lifecycle: advance only when all obligations clear; error codes."""

from __future__ import annotations

import pytest

from werewolf.engine import (
    DoctorSave,
    Game,
    IllegalAction,
    NightKill,
    Post,
    SeerCheck,
    Vote,
)
from werewolf.engine.tests.helpers import config, seats_with


@pytest.fixture
def night_game() -> Game:
    return Game(config(seed=7))


def test_night_obligations_only_specials(night_game: Game) -> None:
    pending = night_game.pending()
    assert set(pending) == set(
        seats_with(night_game, "werewolf")
        + seats_with(night_game, "seer")
        + seats_with(night_game, "doctor")
    )
    assert all(
        pending[s]
        == {
            "werewolf": "night_kill",
            "seer": "seer_check",
            "doctor": "doctor_save",
        }[night_game.role(s)]
        for s in pending
    )


def test_window_holds_until_last_obligation_clears(night_game: Game) -> None:
    turn = night_game.turn_id
    pending = night_game.pending()
    seats = sorted(pending)
    for seat in seats[:-1]:
        night_game.abstain(seat)
        assert night_game.phase == "night"
        assert night_game.turn_id == turn
    night_game.abstain(seats[-1])
    assert night_game.phase != "night"


def test_wolf_chat_never_clears_obligation(night_game: Game) -> None:
    wolf = seats_with(night_game, "werewolf")[0]
    pending_before = night_game.pending()
    night_game.wolf_chat(wolf, "who do we hit?")
    assert night_game.pending() == pending_before


def test_already_acted(night_game: Game) -> None:
    seer = seats_with(night_game, "seer")[0]
    target = night_game.legal_targets(seer)[0]
    night_game.act(seer, SeerCheck(target))
    with pytest.raises(IllegalAction, check=lambda e: e.code == "already_acted"):
        night_game.act(seer, SeerCheck(target))
    with pytest.raises(IllegalAction, check=lambda e: e.code == "already_acted"):
        night_game.abstain(seer)


def test_not_your_turn_villager_at_night(night_game: Game) -> None:
    villager = seats_with(night_game, "villager")[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_your_turn"):
        night_game.act(villager, Vote(target=0))
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_your_turn"):
        night_game.abstain(villager)


def test_wolf_chat_rejected_for_non_wolves(night_game: Game) -> None:
    seer = seats_with(night_game, "seer")[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_your_turn"):
        night_game.wolf_chat(seer, "hello?")


def test_dead_seat_cannot_act() -> None:
    game = Game(config(seed=7))
    wolves = seats_with(game, "werewolf")
    seer, doctor = seats_with(game, "seer")[0], seats_with(game, "doctor")[0]
    villager = seats_with(game, "villager")[0]
    # wolves converge on one villager; doctor saves nobody -> death at night close
    game.act(seer, SeerCheck(wolves[0]))
    game.abstain(doctor)
    for wolf in wolves:
        game.act(wolf, NightKill(villager))
    assert villager not in game.alive()
    with pytest.raises(IllegalAction, check=lambda e: e.code == "dead_seat"):
        game.act(villager, Post(chat="boo"))
    with pytest.raises(IllegalAction, check=lambda e: e.code == "dead_seat"):
        game.abstain(villager)


def test_invalid_target_self_kill_and_wrong_type(night_game: Game) -> None:
    wolf = seats_with(night_game, "werewolf")[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        night_game.act(wolf, NightKill(wolf))
    partner = seats_with(night_game, "werewolf")[1]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        night_game.act(wolf, NightKill(partner))
    with pytest.raises(IllegalAction, check=lambda e: e.code == "bad_schema"):
        night_game.act(wolf, Post(chat="wrong kind of action"))


def test_vote_invalid_target_self() -> None:
    game = Game(config(seed=7))
    # clear night with abstentions to reach day 1 vote quickly (rounds=0)
    game = Game(config(seed=7, rounds=0))
    for seat in sorted(game.pending()):
        game.abstain(seat)
    assert game.phase == "day_vote"
    seat = game.alive()[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(seat, Vote(seat))


def test_too_long_post_and_wolf_chat(night_game: Game) -> None:
    wolf = seats_with(night_game, "werewolf")[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "too_long"):
        night_game.wolf_chat(wolf, "x" * 501)
    game = Game(config(seed=7, rounds=1))
    for seat in sorted(game.pending()):
        game.abstain(seat)
    assert game.phase == "day_discussion"
    seat = game.alive()[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "too_long"):
        game.act(seat, Post(chat="x" * 281))


def test_bad_schema_claimed_role_and_accuse() -> None:
    game = Game(config(seed=7, rounds=1))
    for seat in sorted(game.pending()):
        game.abstain(seat)
    seat = game.alive()[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "bad_schema"):
        game.act(seat, Post(chat="hi", claimed_role="mayor"))
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(seat, Post(chat="hi", accuse=99))


def test_legal_targets_scoped_to_obligation(night_game: Game) -> None:
    wolf = seats_with(night_game, "werewolf")[0]
    targets = night_game.legal_targets(wolf)
    assert wolf not in targets
    assert seats_with(night_game, "werewolf")[1] not in targets
    assert night_game.legal_targets(seats_with(night_game, "villager")[0]) == []


def test_turn_id_changes_each_window() -> None:
    game = Game(config(seed=7))
    seen = {game.turn_id}
    while game.result is None:
        for seat in sorted(game.pending()):
            game.abstain(seat)
        if game.turn_id not in seen:
            seen.add(game.turn_id)
    assert len(seen) > 1
