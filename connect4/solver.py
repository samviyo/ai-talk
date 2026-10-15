"""Negamax with alpha-beta pruning; depth-limited, center-first move ordering."""

from __future__ import annotations

from .engine import Board, COLS

WIN_SCORE = 1000

# Center columns are strongest in Connect Four; ordering speeds up pruning.
_ORDER = sorted(range(COLS), key=lambda c: abs(c - COLS // 2))


def winning_moves(board: Board, player: int) -> set[int]:
    return {c for c in board.legal_moves() if board.apply(c, player).winner() == player}


def blocking_moves(board: Board, player: int) -> set[int]:
    """Columns after which the opponent has no immediate winning move."""
    return {
        c
        for c in board.legal_moves()
        if not winning_moves(board.apply(c, player), -player)
    }


def _negamax(board: Board, player: int, depth: int, alpha: float, beta: float,
             ply: int, tt: dict) -> float:
    won = board.winner()
    if won != 0:
        # The side to move did not make the winning move.
        return -(WIN_SCORE - ply)
    if not board.legal_moves():
        return 0.0
    if depth == 0:
        mine = len(winning_moves(board, player))
        theirs = len(winning_moves(board, -player))
        return float(10 * mine - 10 * theirs)
    key = (board, player, depth)
    if key in tt:
        return tt[key]
    best = float("-inf")
    for c in _ORDER:
        if c not in board.legal_moves():
            continue
        score = -_negamax(board.apply(c, player), -player, depth - 1, -beta, -alpha, ply + 1, tt)
        best = max(best, score)
        alpha = max(alpha, score)
        if alpha >= beta:
            break
    tt[key] = best
    return best


def best_moves(board: Board, player: int, depth: int = 8) -> list[int]:
    tt: dict = {}
    scored = []
    for c in board.legal_moves():
        scored.append((-_negamax(board.apply(c, player), -player, depth - 1,
                                 float("-inf"), float("inf"), 1, tt), c))
    top = max(s for s, _ in scored)
    return [c for s, c in scored if s == top]
