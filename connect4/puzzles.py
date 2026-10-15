"""Deterministic puzzle generation from seeded random play, verified by the solver."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .engine import Board, new_board
from .solver import blocking_moves, winning_moves


@dataclass(frozen=True)
class Puzzle:
    board: Board
    player: int
    answer: frozenset[int]  # set of correct columns


def _candidate(board: Board, player: int, kind: str) -> frozenset[int] | None:
    wins = winning_moves(board, player)
    if kind == "win_in_1":
        return frozenset(wins) if wins else None
    # Exclude positions with an immediate win: blocking wouldn't be the answer.
    if wins:
        return None
    blocks = blocking_moves(board, player)
    # Unambiguous only if at least one legal column loses immediately.
    if not blocks or len(blocks) == len(board.legal_moves()):
        return None
    return frozenset(blocks)


def _playout_positions(rng: random.Random):
    """Yield (board, player) at every step of a random non-terminal playout."""
    board = new_board()
    player = 1
    while board.legal_moves():
        non_losing = [c for c in board.legal_moves() if board.apply(c, player).winner() == 0]
        if not non_losing:
            return
        yield board, player
        board = board.apply(rng.choice(non_losing), player)
        player = -player


def generate(size: int, kind: str, seed: int = 0) -> list[Puzzle]:
    if kind not in ("win_in_1", "block_in_1"):
        raise ValueError(f"unknown puzzle kind: {kind}")
    rng = random.Random(f"{seed}:{kind}")
    puzzles: list[Puzzle] = []
    seen: set[Board] = set()
    # Scan every position of each playout; candidate positions are rare at game end.
    while len(puzzles) < size:
        for board, player in _playout_positions(rng):
            if board in seen:
                continue
            answer = _candidate(board, player, kind)
            if answer is None:
                continue
            seen.add(board)
            puzzles.append(Puzzle(board, player, answer))
            if len(puzzles) == size:
                break
    return puzzles
