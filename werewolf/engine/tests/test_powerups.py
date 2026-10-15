"""Power-ups: purchase rules, resolution order, deferred effects, visibility."""

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
from werewolf.engine.tests.helpers import config, end_discussion, end_night, seats_with, to_vote


def _night_seats(game: Game) -> tuple[int, int, list[int], int]:
    seer = seats_with(game, "seer")[0]
    doctor = seats_with(game, "doctor")[0]
    wolves = seats_with(game, "werewolf")
    villager = seats_with(game, "villager")[0]
    return seer, doctor, wolves, villager


def test_unknown_item_and_wrong_window() -> None:
    game = Game(config(seed=7))
    seer, doctor, wolves, villager = _night_seats(game)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_available"):
        game.apply_powerup(villager, "bribe")
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_available"):
        game.apply_powerup(villager, "extra_post")  # night, not discussion
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_available"):
        game.apply_powerup(villager, "frame")  # wolves only
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_available"):
        game.apply_powerup(wolves[0], "shield")  # non-wolves only


def test_once_per_day_and_per_game_limits() -> None:
    game = Game(config(seed=7))
    end_night(game)
    a, b = game.alive()[0], game.alive()[1]
    game.apply_powerup(a, "whisper", target=b, msg="psst")
    with pytest.raises(IllegalAction, check=lambda e: e.code == "already_bought"):
        game.apply_powerup(a, "whisper", target=b, msg="again")
    # a nomination gets a vote window where double_vote is legal
    game.act(a, Post(chat=f"seat {b}", nominate=b), now=0.0)
    to_vote(game)
    assert game.phase == "day_vote"
    game.apply_powerup(a, "double_vote")
    with pytest.raises(IllegalAction, check=lambda e: e.code == "already_bought"):
        game.apply_powerup(a, "double_vote")


def _clear_night(game: Game) -> None:
    for seat in sorted(game.pending()):
        game.abstain(seat)


def _skip_day(game: Game) -> None:
    game.close_discussion()
    while game.phase == "defence":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    assert game.phase == "night"


def test_frame_makes_target_read_wolf() -> None:
    game = Game(config(seed=7))
    seer, doctor, wolves, villager = _night_seats(game)
    game.apply_powerup(wolves[0], "frame", target=villager)
    game.act(seer, SeerCheck(villager))
    for wolf in wolves:
        game.abstain(wolf)
    game.abstain(doctor)
    result = next(e for e in game.events(seer) if e.kind == "seer_result")
    assert result.data["role"] == "werewolf"
    assert game.role(villager) == "villager"  # the frame lies, the role doesn't
    # next night the frame is gone
    assert game.phase == "day_discussion"
    _skip_day(game)
    game.act(seer, SeerCheck(villager))
    _clear_night(game)
    result = [e for e in game.events(seer) if e.kind == "seer_result"][-1]
    assert result.data["role"] == "villager"


def test_shield_blocks_the_kill() -> None:
    game = Game(config(seed=7))
    _clear_night(game)
    a = seats_with(game, "villager")[0]
    game.apply_powerup(a, "shield")
    _skip_day(game)
    seer, doctor, wolves, _ = _night_seats(game)
    for wolf in wolves:
        game.act(wolf, NightKill(a))
    game.abstain(seer)
    game.abstain(doctor)
    result = next(e for e in game.events(None) if e.kind == "night_result")
    assert result.data["killed"] is None
    assert a in game.alive()
    assert any(e.kind == "no_death" for e in game.events(None))


def test_shield_unavailable_on_last_day() -> None:
    game = Game(config(seed=7, max_days=1))
    end_night(game)
    v = seats_with(game, "villager")[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "not_available"):
        game.apply_powerup(v, "shield")


def test_double_vote_counts_twice_and_shows_weight() -> None:
    game = Game(config(seed=7))
    end_night(game)
    alive = game.alive()
    a, b = alive[0], alive[1]
    game.act(a, Post(chat=f"seat {b}", nominate=b), now=0.0)
    to_vote(game)
    game.apply_powerup(a, "double_vote")
    for seat in sorted(game.pending()):
        if seat == b:
            game.abstain(seat)
        else:
            game.act(seat, Vote(b))
    tally = next(e for e in game.events(None) if e.kind == "vote_tally")
    ballot = next(x for x in tally.data["ballots"] if x["seat"] == a)
    assert ballot["weight"] == 2


def test_extra_post_raises_the_daily_limit() -> None:
    game = Game(config(seed=7, max_posts=1))
    end_night(game)
    a = game.alive()[0]
    game.apply_powerup(a, "extra_post")
    game.act(a, Post(chat="one"), now=0.0)
    game.act(a, Post(chat="two"), now=6.0)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "no_posts_left"):
        game.act(a, Post(chat="three"), now=12.0)


def test_whisper_reaches_target_at_window_close_only() -> None:
    game = Game(config(seed=7))
    end_night(game)
    a, b, c = game.alive()[0], game.alive()[1], game.alive()[2]
    game.apply_powerup(a, "whisper", target=b, msg="I trust you")
    assert not any(e.kind == "whisper" for e in game.events(b))
    end_discussion(game)
    whisper = next(e for e in game.events(b) if e.kind == "whisper")
    assert whisper.data == {"seat": a, "target": b, "msg": "I trust you"}
    assert not any(e.kind == "whisper" for e in game.events(c))
    omniscient = next(e for e in game.events(None) if e.kind == "whisper")
    assert omniscient.data["msg"] == "I trust you"


def test_whisper_target_rules() -> None:
    game = Game(config(seed=7))
    end_night(game)
    a, b = game.alive()[0], game.alive()[1]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.apply_powerup(a, "whisper", target=a, msg="me")
    with pytest.raises(IllegalAction, check=lambda e: e.code == "too_long"):
        game.apply_powerup(a, "whisper", target=b, msg="x" * 281)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "bad_schema"):
        game.apply_powerup(a, "whisper", target=b, msg="")


def test_shop_reports_availability_without_side_effects() -> None:
    game = Game(config(seed=7))
    seer, doctor, wolves, villager = _night_seats(game)
    shop = {i["item"]: i for i in game.shop(wolves[0])}
    assert shop["frame"]["available"] is True
    assert shop["shield"]["available"] is False
    assert shop["double_vote"]["available"] is False
    shop_v = {i["item"]: i for i in game.shop(villager)}
    assert shop_v["frame"]["available"] is False
    assert shop_v["extra_post"]["available"] is False  # night, not discussion
    end_night(game)
    shop_v = {i["item"]: i for i in game.shop(villager)}
    assert shop_v["extra_post"]["available"] is True


def test_powerup_event_private_to_buyer() -> None:
    game = Game(config(seed=7))
    end_night(game)
    a, b = game.alive()[0], game.alive()[1]
    if game.role(a) == "werewolf":
        a, b = b, a
    game.apply_powerup(a, "extra_post")
    assert any(e.kind == "powerup" for e in game.events(a))
    assert not any(e.kind == "powerup" for e in game.events(b))
    assert any(e.kind == "powerup" for e in game.events(None))
