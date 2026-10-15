"""Picks a uniformly random legal column."""

from __future__ import annotations

import random

from ..engine import Board

_rng = random.Random()


def choose(board: Board, player: int) -> int:
    return _rng.choice(board.legal_moves())
