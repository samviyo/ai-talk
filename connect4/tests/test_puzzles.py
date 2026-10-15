"""Puzzle tests: answers verified independently by brute-force one-ply lookahead."""

from __future__ import annotations

from connect4 import puzzles
from connect4.engine import COLS


def brute_win_in_1(board, player) -> set[int]:
    return {c for c in range(COLS) if c in board.legal_moves()
            and board.apply(c, player).winner() == player}


def brute_block_in_1(board, player) -> set[int]:
    """Columns leaving the opponent no immediate winning move."""
    out = set()
    for c in board.legal_moves():
        after = board.apply(c, player)
        opp_wins = {d for d in after.legal_moves()
                    if after.apply(d, -player).winner() == -player}
        if not opp_wins:
            out.add(c)
    return out


def test_win_in_1_answers_match_brute_force():
    for p in puzzles.generate(15, "win_in_1", seed=7):
        assert p.answer == frozenset(brute_win_in_1(p.board, p.player))
        assert p.answer


def test_block_in_1_answers_match_brute_force():
    for p in puzzles.generate(15, "block_in_1", seed=7):
        # No immediate win for the side to move, else blocking is not the answer.
        assert not brute_win_in_1(p.board, p.player)
        assert p.answer == frozenset(brute_block_in_1(p.board, p.player))
        assert p.answer
        # Unambiguous: at least one legal move loses immediately.
        assert len(p.answer) < len(p.board.legal_moves())


def test_generation_is_deterministic():
    a = puzzles.generate(10, "win_in_1", seed=3)
    b = puzzles.generate(10, "win_in_1", seed=3)
    assert a == b


def test_players_alternate_sanity():
    for p in puzzles.generate(10, "win_in_1", seed=1):
        assert p.player in (1, -1)
        assert not p.board.is_terminal()
