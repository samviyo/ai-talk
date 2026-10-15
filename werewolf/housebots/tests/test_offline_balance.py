"""Offline balance harness smoke test: house-bot brains drive a full engine game."""
from __future__ import annotations

from werewolf.housebots.offline import balance, play_game

PRACTICE_LINEUP = ["baseline", "baseline", "accuser", "quiet", "random", "baseline", "baseline"]


def test_offline_game_completes() -> None:
    assert play_game(PRACTICE_LINEUP, seed=1000) in ("village", "wolves", "draw")


def test_practice_lineup_is_winnable_by_village_sometimes() -> None:
    # Guard against a degenerate table where the village can never win.
    results = balance(PRACTICE_LINEUP, 40)
    assert results["village"] > 0
