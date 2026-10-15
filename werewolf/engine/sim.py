"""Drive a Game to completion with per-seat policies; for tests and balance sims."""

from __future__ import annotations

import random
from typing import Callable

from werewolf.engine.core import (
    Action,
    Config,
    DoctorSave,
    Game,
    NightKill,
    Obligation,
    SeerCheck,
    Vote,
)

Policy = Callable[[Game, int, Obligation], "Action | None"]


def play(config: Config, policy: Policy) -> Game:
    """Run windows until the game ends; a policy returning None abstains that seat."""
    game = Game(config)
    while game.result is None:
        for seat in sorted(game.pending()):
            action = policy(game, seat, game.pending()[seat])
            if action is None:
                game.abstain(seat)
            else:
                game.act(seat, action)
    return game


_TARGET_ACTIONS: dict[str, type] = {
    "night_kill": NightKill,
    "seer_check": SeerCheck,
    "doctor_save": DoctorSave,
    "vote": Vote,
}


def random_policy(rng: random.Random) -> Policy:
    """Random legal targets with occasional abstention; posts abstain."""

    def decide(game: Game, seat: int, obligation: Obligation) -> Action | None:
        if obligation == "post":
            return None
        targets = game.legal_targets(seat)
        if not targets or rng.random() < 0.2:
            return None
        return _TARGET_ACTIONS[obligation](rng.choice(targets))

    return decide
