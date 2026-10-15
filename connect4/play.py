"""Game loop shared by the eval harness and the tournament."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .bots.base import Choose, unwrap
from .engine import Board, new_board


@dataclass
class MoveLog:
    latencies: list[float] = field(default_factory=list)
    prompt_tokens: list[int] = field(default_factory=list)
    completion_tokens: list[int] = field(default_factory=list)
    illegal: int = 0

    def record(self, column, latency_s: float, prompt_tokens, completion_tokens) -> bool:
        """Record a move; returns True if legal."""
        self.latencies.append(latency_s)
        if prompt_tokens is not None:
            self.prompt_tokens.append(prompt_tokens)
        if completion_tokens is not None:
            self.completion_tokens.append(completion_tokens)
        ok = isinstance(column, int) and not isinstance(column, bool) and 0 <= column < 7
        if not ok:
            self.illegal += 1
        return ok


def play_game(bots: dict[int, Choose], max_moves: int = 42) -> tuple[int, dict[int, MoveLog]]:
    """Play one game; returns (winner, logs), winner 0 = draw or move cap.

    Illegal moves get a random legal fallback but are still counted illegal.
    """
    import random

    board = new_board()
    player = 1
    logs = {p: MoveLog() for p in bots}
    rng = random.Random()
    for _ in range(max_moves):
        if board.is_terminal():
            break
        bot = bots[player]
        start = time.perf_counter()
        raw = bot(board, player)
        column, result = unwrap(raw)
        latency = result.latency_s or (time.perf_counter() - start)
        legal_col = column if (isinstance(column, int) and not isinstance(column, bool)
                               and column in board.legal_moves()) else None
        logs[player].record(column, latency, result.prompt_tokens, result.completion_tokens)
        if legal_col is None:
            legal_col = rng.choice(board.legal_moves())
        board = board.apply(legal_col, player)
        player = -player
    return board.winner(), logs
