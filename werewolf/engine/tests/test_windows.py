"""Window/obligation lifecycle: advance only when obligations clear; error codes."""

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
from werewolf.engine.tests.helpers import (
    config,
    end_discussion,
    end_night,
    seats_with,
    to_vote,
)


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
    game.act(seer, SeerCheck(wolves[0]))
    game.abstain(doctor)
    for wolf in wolves:
        game.act(wolf, NightKill(villager))
    while game.phase == "reveal":
        game.advance_reveal()
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


def test_vote_restricted_to_standing_nominees() -> None:
    game = Game(config(seed=7))
    end_night(game)
    alive = game.alive()
    nominator, target = alive[0], alive[1]
    game.act(nominator, Post(chat=f"seat {target} is sus", nominate=target), now=0.0)
    to_vote(game)
    assert game.phase == "day_vote"
    voter = alive[2]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(voter, Vote(alive[3]))
    game.act(voter, Vote(target))


def test_no_nominee_skips_the_vote() -> None:
    game = Game(config(seed=7))
    end_night(game)
    game.act(game.alive()[0], Post(chat="quiet day"), now=0.0)
    end_discussion(game)
    assert game.phase == "night"
    assert game.day == 2
    skipped = next(e for e in game.events(None) if e.kind == "vote_skipped")
    assert skipped.data["silent"] == [s for s in game.alive() if s != game.alive()[0]]
    assert not any(e.kind == "vote_tally" for e in game.events(None))


def test_vote_skip_on_max_days_is_a_draw() -> None:
    game = Game(config(seed=7, max_days=1))
    end_night(game)
    end_discussion(game)
    assert game.result == "draw"


def test_too_long_post_and_wolf_chat(night_game: Game) -> None:
    wolf = seats_with(night_game, "werewolf")[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "too_long"):
        night_game.wolf_chat(wolf, "x" * 501)
    end_night(night_game)
    seat = night_game.alive()[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "too_long"):
        night_game.act(seat, Post(chat="x" * 281), now=0.0)


def test_bad_schema_claimed_role_and_accuse() -> None:
    game = Game(config(seed=7))
    end_night(game)
    seat = game.alive()[0]
    with pytest.raises(IllegalAction, check=lambda e: e.code == "bad_schema"):
        game.act(seat, Post(chat="hi", claimed_role="mayor"), now=0.0)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(seat, Post(chat="hi", accuse=99), now=0.0)


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
        if game.phase == "day_discussion":
            game.close_discussion()
            continue
        for seat in sorted(game.pending()):
            game.abstain(seat)
        seen.add(game.turn_id)
    assert len(seen) > 1


def test_max_posts_limit() -> None:
    # The 5s gap is the server floor's job now; the engine keeps max_posts.
    game = Game(config(seed=7, max_posts=2))
    end_night(game)
    seat = game.alive()[0]
    game.act(seat, Post(chat="one"), now=0.0)
    game.act(seat, Post(chat="two"), now=1.0)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "no_posts_left"):
        game.act(seat, Post(chat="three"), now=2.0)


def test_reply_to_must_be_public_post_this_day() -> None:
    game = Game(config(seed=7))
    end_night(game)
    a, b = game.alive()[0], game.alive()[1]
    game.act(a, Post(chat="hello"), now=0.0)
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(b, Post(chat="reply", reply_to=99), now=0.0)
    game.act(b, Post(chat="reply", reply_to=0), now=0.0)
    end_discussion(game)
    end_window_night(game)
    # a post from yesterday is off-limits
    with pytest.raises(IllegalAction, check=lambda e: e.code == "invalid_target"):
        game.act(a, Post(chat="late reply", reply_to=0), now=0.0)


def end_window_night(game: Game) -> None:
    """Abstain defences and the vote or skip, then close the night."""
    while game.phase == "defence":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    if game.phase == "day_vote":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    for seat in sorted(game.pending()):
        game.abstain(seat)
    assert game.phase == "day_discussion"
