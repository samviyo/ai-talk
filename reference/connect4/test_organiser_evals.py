"""End-to-end harness tests on deterministic bots; no network."""

from __future__ import annotations

import pytest

from reference.connect4 import organiser_evals as evals
from connect4.bots import column0_bot, random_bot


def _by_name(checks):
    return {name: (passed, detail) for name, passed, detail in checks}


def test_random_bot_passes_legality_and_latency_gates():
    checks, stats = evals.evaluate(
        random_bot.choose, games=6, puzzle_size=10, seed=5, model=None)
    by_name = _by_name(checks)
    assert by_name["0 illegal moves"][0]
    assert by_name["p95 latency < 3s"][0]
    assert stats["moves"] > 0
    # No token usage -> cost gate is skipped, not failed.
    assert not any(name.startswith("cost/move") for name, _, _ in checks)


def test_column0_bot_fails_puzzle_gates():
    checks, _ = evals.evaluate(
        column0_bot.choose, games=2, puzzle_size=10, seed=5, model=None)
    by_name = _by_name(checks)
    assert not by_name["win-in-1 >= 95%"][0]
    assert not by_name["block-in-1 >= 95%"][0]


def test_negative_control_reports_live_suite():
    failed, detail = evals.negative_control(puzzle_size=10, seed=5)
    assert failed, f"negative control must fail the puzzle suite: {detail}"


def test_main_exit_codes(capsys):
    rc_bad = evals.main([
        "--bot", "connect4.bots.column0_bot:choose",
        "--games", "2", "--puzzles", "10", "--seed", "5",
        "--no-negative-control",
    ])
    assert rc_bad != 0
    out = capsys.readouterr().out
    assert "RED" in out

    rc_good = evals.main([
        "--bot", "connect4.bots.random_bot:choose",
        "--games", "4", "--puzzles", "10", "--seed", "5",
        "--no-negative-control",
    ])
    out = capsys.readouterr().out
    # random_bot can't pass the puzzle/beat-random gates in general; only
    # assert the scoreboard rendered and the legality gate passed.
    assert "0 illegal moves" in out
    assert rc_good in (0, 1)


def test_illegal_moves_counted_for_garbage_bot():
    def garbage_bot(board, player):
        return "not a column"

    checks, stats = evals.evaluate(
        garbage_bot, games=2, puzzle_size=5, seed=1, model=None)
    by_name = _by_name(checks)
    assert not by_name["0 illegal moves"][0]
    assert stats["illegal"] > 0


def test_cost_gate_appears_with_usage():
    def pricey_bot(board, player):
        from connect4.bots.base import MoveResult
        return MoveResult(column=board.legal_moves()[0], latency_s=0.01,
                          prompt_tokens=100000, completion_tokens=100000)

    checks, _ = evals.evaluate(
        pricey_bot, games=1, puzzle_size=3, seed=1, model="gpt-4o-mini")
    cost_checks = [c for c in checks if c[0].startswith("cost/move")]
    assert cost_checks
    assert not cost_checks[0][1]  # absurd usage must fail the cost gate
