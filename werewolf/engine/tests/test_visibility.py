"""Visibility: posts public when accepted, ballots until resolution, per-seat views."""

from __future__ import annotations

import random

import pytest

from werewolf.engine import Game, Post, Vote
from werewolf.engine.sim import play, random_policy
from werewolf.engine.tests.helpers import config, end_night, seats_with, to_vote


def to_discussion(game: Game) -> None:
    end_night(game)
    assert game.phase == "day_discussion"


def test_posts_public_as_soon_as_accepted() -> None:
    game = Game(config(seed=7))
    to_discussion(game)
    alive = game.alive()
    first, rest = alive[0], alive[1:]
    game.act(first, Post(chat="i am the seer, trust me", claimed_role="seer"), now=0.0)
    for viewer in rest:
        revealed = [e for e in game.events(viewer) if e.kind == "posts_revealed"]
        assert revealed, "post not visible to another seat during discussion"
        assert revealed[-1].data["posts"][0]["chat"] == "i am the seer, trust me"


def test_revealed_post_fields_match_board_shape() -> None:
    game = Game(config(seed=7))
    to_discussion(game)
    seat = game.alive()[0]
    game.act(
        seat, Post(chat="hello", claimed_role="doctor", accuse=game.alive()[1]), now=0.0
    )
    reveal = next(e for e in game.events(None) if e.kind == "posts_revealed")
    post = next(p for p in reveal.data["posts"] if p["seat"] == seat)
    assert set(post) == {
        "id", "seat", "day", "round", "chat", "claimed_role", "accuse",
        "reply_to", "nominate", "second",
    }
    assert post["day"] == 1
    assert post["claimed_role"] == "doctor"


def test_ballots_invisible_until_resolution() -> None:
    game = Game(config(seed=7))
    to_discussion(game)
    alive = game.alive()
    first, rest = alive[0], alive[1:]
    target = rest[0]
    game.act(first, Post(chat="nom", nominate=target), now=0.0)
    to_vote(game)
    game.act(first, Vote(target))
    for viewer in rest:
        assert not any(e.kind == "vote_tally" for e in game.events(viewer))
    for seat in rest:
        if seat == target:
            game.abstain(seat)
        else:
            game.act(seat, Vote(target))
    tally = next(e for e in game.events(first) if e.kind == "vote_tally")
    assert tally.data["eliminated"] == target
    assert any(
        b["seat"] == first and b["target"] == target for b in tally.data["ballots"]
    )
    assert all("weight" in b for b in tally.data["ballots"])


def test_role_assignment_private() -> None:
    game = Game(config(seed=7))
    seer = seats_with(game, "seer")[0]
    own = [e for e in game.events(seer) if e.kind == "role_assignment"]
    assert len(own) == 1 and own[0].data["seat"] == seer
    other = seats_with(game, "villager")[0]
    seen = [e.data["seat"] for e in game.events(other) if e.kind == "role_assignment"]
    assert seen == [other]


def test_wolf_sees_partners_and_wolf_chat_only() -> None:
    game = Game(config(seed=7))
    wolves = seats_with(game, "werewolf")
    me, partner = wolves
    assignment = next(e for e in game.events(me) if e.kind == "role_assignment")
    assert assignment.data["partners"] == [partner]
    game.wolf_chat(partner, "psst")
    assert any(e.kind == "wolf_chat" for e in game.events(me))
    villager = seats_with(game, "villager")[0]
    assert not any(e.kind == "wolf_chat" for e in game.events(villager))
    assert not any(
        e.kind == "role_assignment" and e.data["seat"] != villager
        for e in game.events(villager)
    )


def test_omniscient_sees_everything() -> None:
    game = Game(config(seed=7))
    assert len(game.events(None)) == game.config.seats + 1  # assignments + first phase
    game.wolf_chat(seats_with(game, "werewolf")[0], "hi")
    assert any(e.kind == "wolf_chat" for e in game.events(None))


def test_mentions_reach_only_the_named_seat() -> None:
    game = Game(config(seed=7))
    to_discussion(game)
    a, b, c = game.alive()[0], game.alive()[1], game.alive()[2]
    game.act(a, Post(chat=f"hey seat {b}, talk to me"), now=0.0)
    mentions_b = [e.data for e in game.events(b) if e.kind == "mention"]
    assert len(mentions_b) == 1
    assert mentions_b[0]["from"] == a and mentions_b[0]["seat"] == b
    assert not any(e.kind == "mention" for e in game.events(c))
    # the same pair does not wake twice in a day
    game.act(a, Post(chat=f"seat {b} again"), now=6.0)
    assert len([e for e in game.events(b) if e.kind == "mention"]) == 1


def test_events_rejects_unknown_seat() -> None:
    game = Game(config(seed=7))
    with pytest.raises(ValueError):
        game.events(99)


def role_mentions(event, seat: int) -> bool:
    """True if the event reveals `seat`'s role to its audience."""
    data = event.data
    if event.kind == "role_assignment":
        return data["seat"] == seat
    if event.kind == "seer_result":
        return data["seat"] == seat
    if event.kind == "death":
        return data["seat"] == seat
    return False


def test_leak_property_random_games() -> None:
    """No viewer ever learns a living stranger's role outside allowed channels."""
    for seed in range(100):
        game = play(
            config(seats=7, wolves=2, max_days=5, seed=seed),
            random_policy(random.Random(10_000 + seed)),
        )
        wolves = set(seats_with(game, "werewolf"))
        seer = seats_with(game, "seer")[0]
        # dead-at-event-time from the omniscient log; per-viewer order may differ
        dead_at: dict[int, int] = {}
        for i, event in enumerate(game.events(None)):
            if event.kind == "death":
                dead_at.setdefault(event.data["seat"], i)
        for viewer in range(7):
            for i, event in enumerate(game.events(None)):
                if event.audience is not None and viewer not in event.audience:
                    continue
                for seat in range(7):
                    if seat == viewer or not role_mentions(event, seat):
                        continue
                    allowed = (
                        dead_at.get(seat, 1 << 30) <= i
                        or (viewer in wolves and seat in wolves)
                        or (viewer == seer and event.kind == "seer_result")
                    )
                    assert allowed, (
                        f"seed {seed}: viewer {viewer} learned seat {seat}'s role "
                        f"via {event.kind} while that seat was alive"
                    )
