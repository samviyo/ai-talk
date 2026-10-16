"""Datasets the eval harness provides: solver-backed puzzles and vs-random games.

Vocabulary (used in code and docs): a *task* is one dataset row, a *trial* is
one run of a task, the *transcript* is the per-move log a game task records,
the *outcome* is the task's output (a move for puzzle rows, a game result for
game rows), a *scorer* maps (input, output, expected) to 0..1 or None, a
*gate* is a threshold on a scorer mean, and a *suite* is the set of datasets
plus the team's scorers and gates.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any

from . import puzzles
from .bots import random_bot
from .bots.base import unwrap
from .engine import Board, new_board


@dataclass
class MoveRecord:
    """One entry of a game transcript: the outcome plus what it cost."""
    column: int | None
    legal: bool
    latency_s: float
    prompt_tokens: int | None
    completion_tokens: int | None


@dataclass
class GameOutcome:
    winner: int  # 1 = task bot, -1 = random bot, 0 = draw or move cap
    moves: list[MoveRecord] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "winner": self.winner,
            "moves": [
                {
                    "column": m.column,
                    "legal": m.legal,
                    "latency_s": m.latency_s,
                    "prompt_tokens": m.prompt_tokens,
                    "completion_tokens": m.completion_tokens,
                }
                for m in self.moves
            ],
        }


def puzzle_rows(kind: str, size: int, seed: int) -> list[dict[str, Any]]:
    """Rows for `puzzles_win` (win_in_1) or `puzzles_block` (block_in_1).

    The solver supplies ground truth: `expected.columns` lists the correct columns.
    """
    rows = []
    for p in puzzles.generate(size, kind, seed=seed):
        rows.append({
            "input": {
                "board": list(p.board.grid),
                "player": p.player,
                "legal": p.board.legal_moves(),
            },
            "expected": {"columns": sorted(p.answer)},
        })
    return rows


def _grid_to_board(grid) -> Board:
    return Board(tuple(tuple(col) for col in grid))


def game_row(seed: int, first: bool) -> dict[str, Any]:
    return {"input": {"seed": seed, "first": first}, "expected": None}


def run_puzzle_task(bot, input: dict[str, Any]) -> dict[str, Any]:
    """Task for a puzzle row: one move; the outcome is the chosen column."""
    board = _grid_to_board(input["board"])
    start = time.perf_counter()
    raw = bot(board, input["player"])
    column, result = unwrap(raw)
    latency = result.latency_s or (time.perf_counter() - start)
    return {
        "column": column if isinstance(column, int) and not isinstance(column, bool) else None,
        "latency_s": latency,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
    }


def run_game_task(bot, input: dict[str, Any]) -> dict[str, Any]:
    """Task for a `vs_random` row: play a full game; the outcome is the result.

    Seeding random off the row makes replays comparable, except when the bot's own moves change the position.
    """
    rng = random.Random(input["seed"])
    board = new_board()
    first = bool(input["first"])
    outcome = GameOutcome(winner=0)
    to_move = first  # True when the task bot moves now

    def random_side() -> int:
        return rng.choice(board.legal_moves())

    for _ in range(42):
        if board.is_terminal():
            break
        if to_move:
            start = time.perf_counter()
            raw = bot(board, 1)
            column, result = unwrap(raw)
            latency = result.latency_s or (time.perf_counter() - start)
            is_int = isinstance(column, int) and not isinstance(column, bool)
            legal = is_int and column in board.legal_moves()
            outcome.moves.append(MoveRecord(
                column=column if is_int else None,
                legal=legal,
                latency_s=latency,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
            ))
            chosen = column if legal else rng.choice(board.legal_moves())
            board = board.apply(chosen, 1)
        else:
            board = board.apply(random_side(), -1)
        to_move = not to_move
    outcome.winner = board.winner()
    return outcome.as_dict()


def vs_random_rows(games: int, seed: int) -> list[dict[str, Any]]:
    """Rows for `vs_random`: alternate who moves first across seeds."""
    return [game_row(seed=seed + i, first=(i % 2 == 0)) for i in range(games)]
