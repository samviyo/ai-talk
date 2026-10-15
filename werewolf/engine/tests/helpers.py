"""Shared test helpers: find seats by role, scripted policies, window drivers."""

from __future__ import annotations

from werewolf.engine import Action, Config, Game, Obligation, Post


def config(
    seats: int = 7,
    wolves: int = 2,
    max_days: int = 5,
    seed: int = 7,
    discussion_s: float = 60.0,
    max_posts: int = 3,
):
    return Config(
        seats=seats,
        wolves=wolves,
        max_days=max_days,
        seed=seed,
        discussion_s=discussion_s,
        max_posts=max_posts,
    )


def seats_with(game: Game, role: str) -> list[int]:
    return [s for s in range(game.config.seats) if game.role(s) == role]


def end_night(game: Game) -> None:
    """Close the open night window with abstentions."""
    assert game.phase == "night"
    for seat in sorted(game.pending()):
        game.abstain(seat)


def end_discussion(game: Game, now: float = 0.0) -> None:
    """Close the open discussion window at its deadline."""
    assert game.phase == "day_discussion"
    game.close_discussion()


def end_window(game: Game) -> None:
    """Close whatever timed/obligation window is open with abstentions."""
    while game.phase == "defence":
        for seat in sorted(game.pending()):
            game.abstain(seat)
    if game.phase in ("night", "day_vote"):
        for seat in sorted(game.pending()):
            game.abstain(seat)
    elif game.phase == "day_discussion":
        game.close_discussion()


def to_vote(game: Game) -> None:
    """Drive from discussion to the vote, abstaining defences."""
    end_discussion(game)
    while game.phase == "defence":
        for seat in sorted(game.pending()):
            game.abstain(seat)


def scripted(**overrides: Action | None):
    """Policy applying per-obligation overrides; defaults: post, else abstain."""

    def decide(game: Game, seat: int, obligation: Obligation) -> Action | None:
        if obligation in overrides:
            action = overrides[obligation]
            if isinstance(action, Post):
                # let one override serve every seat without identical text
                return Post(
                    chat=action.chat.format(seat=seat),
                    claimed_role=action.claimed_role,
                    accuse=action.accuse,
                    reply_to=action.reply_to,
                    nominate=action.nominate,
                    second=action.second,
                )
            return action
        return None

    return decide
