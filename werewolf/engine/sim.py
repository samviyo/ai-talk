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
    Post,
    SeerCheck,
    Vote,
)

# obligation is "discuss" during the live discussion window, which has no obligation.
Policy = Callable[[Game, int, str, float], "Action | None"]


def play(
    config: Config,
    policy: Policy,
    *,
    step_s: float = 6.0,
    post_limit_s: float = 30.0,
) -> Game:
    """Run windows until the game ends; None means the seat stays quiet/abstains."""
    game = Game(config)
    while game.result is None:
        if game.phase == "day_discussion":
            now = 0.0
            while now <= post_limit_s:
                for seat in game.alive():
                    if game.posts_left(seat) <= 0:
                        continue
                    action = policy(game, seat, "discuss", now)
                    if action is None:
                        continue
                    try:
                        game.act(seat, action, now=now)
                    except Exception:
                        continue
                now += step_s
            game.close_discussion()
            continue
        if game.phase == "reveal":
            game.advance_reveal()
            continue
        for seat in sorted(game.pending()):
            obligation = game.pending()[seat]
            action = policy(game, seat, obligation, 0.0)
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
    """Random legal targets with occasional abstention; some small talk."""

    def decide(game: Game, seat: int, obligation: str, now: float) -> Action | None:
        if obligation == "discuss":
            if rng.random() < 0.7:
                return None
            return Post(chat=f"seat {seat} says hello")
        if obligation == "defence":
            return Post(chat=f"seat {seat} is innocent")
        targets = game.legal_targets(seat)
        if not targets or rng.random() < 0.2:
            return None
        return _TARGET_ACTIONS[obligation](rng.choice(targets))

    return decide
