"""Shared test helpers: find seats by role, scripted policies."""

from __future__ import annotations

from werewolf.engine import Action, Config, Game, Obligation, Post


def config(seats: int = 7, wolves: int = 2, rounds: int = 1, max_days: int = 5, seed: int = 7):
    return Config(
        seats=seats, wolves=wolves, discussion_rounds=rounds, max_days=max_days, seed=seed
    )


def seats_with(game: Game, role: str) -> list[int]:
    return [s for s in range(game.config.seats) if game.role(s) == role]


def scripted(**overrides: Action | None):
    """Policy applying per-obligation overrides; defaults: post, else abstain."""

    def decide(game: Game, seat: int, obligation: Obligation) -> Action | None:
        if obligation in overrides:
            action = overrides[obligation]
            if obligation == "post" and isinstance(action, Post):
                # let one override serve every seat without identical text
                return Post(
                    chat=action.chat.format(seat=seat),
                    claimed_role=action.claimed_role,
                    accuse=action.accuse,
                )
            return action
        if obligation == "post":
            return Post(chat=f"seat {seat} here")
        return None

    return decide
