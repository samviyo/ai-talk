"""Negative control: always column 0. Must fail the eval gates."""

from __future__ import annotations

from ..engine import Board


def choose(board: Board, player: int) -> int:
    return 0
