"""End-to-end harness tests via the CLI/public entry; no network, offline Eval."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from connect4 import evals
from connect4.bots.base import MoveResult
from connect4.solver import blocking_moves, winning_moves

TEAM_EVALS = Path(evals.__file__).parent / "team_evals"
SCORER_FILE = TEAM_EVALS / "workshop_scorers.py"
GATES_FILE = TEAM_EVALS / "gates.toml"


@pytest.fixture
def team_scorers():
    """Let a test install a scorer module + gates, then restore the kit."""
    original_gates = GATES_FILE.read_text()
    yield
    SCORER_FILE.unlink(missing_ok=True)
    GATES_FILE.write_text(original_gates)
    for m in list(sys.modules):
        if m.startswith("connect4.team_evals."):
            del sys.modules[m]


def _install_scorers(code: str, gates: str | None = None) -> None:
    SCORER_FILE.write_text(code)
    if gates is not None:
        GATES_FILE.write_text(gates)


def _run(bot: str = "connect4.bots.random_bot:choose", games=2, puzzles=5, trials=1):
    return evals.main(["--bot", bot, "--games", str(games),
                       "--puzzles", str(puzzles), "--trials", str(trials)])


def test_no_scorers_is_red(capsys):
    rc = _run()
    out = capsys.readouterr().out
    assert rc != 0
    assert "no scorers yet" in out


def test_always_one_scorer_fails_negative_control(capsys, team_scorers):
    _install_scorers(
        "def score_perfect(input, output, expected=None, **kw):\n"
        "    return 1.0\n",
        "[score_perfect]\nmin = 0.95\n")
    rc = _run(games=2, puzzles=4)
    out = capsys.readouterr().out
    assert rc != 0
    assert "your suite passes column0_bot" in out


def test_column_scorer_green_for_solver_bot_red_for_column0(capsys, team_scorers):
    _install_scorers(
        "def score_puzzle_column(input, output, expected=None, **kw):\n"
        "    if not expected or \"columns\" not in expected:\n"
        "        return None\n"
        "    return 1.0 if output.get(\"column\") in expected[\"columns\"] else 0.0\n",
        "[score_puzzle_column]\nmin = 0.95\n")

    # A tiny perfect test bot: the solver decides, wrapped in the test.
    def perfect_bot(board, player):
        wins = sorted(winning_moves(board, player))
        if wins:
            return MoveResult(column=wins[0], latency_s=0.01)
        blocks = sorted(blocking_moves(board, player))
        if blocks:
            return MoveResult(column=blocks[0], latency_s=0.01)
        legal = board.legal_moves()
        return MoveResult(column=legal[len(legal) // 2], latency_s=0.01)

    sys.modules["perfect_test_bot"] = SimpleNamespace(choose=perfect_bot)
    try:
        rc = _run(bot="perfect_test_bot:choose", games=2, puzzles=5)
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "your suite passes column0_bot" not in out

        rc0 = _run(bot="connect4.bots.column0_bot:choose", games=2, puzzles=5)
        out0 = capsys.readouterr().out
        assert rc0 != 0
        assert "your suite passes column0_bot" not in out0
    finally:
        del sys.modules["perfect_test_bot"]


def test_raising_scorer_is_red_row(capsys, team_scorers):
    _install_scorers(
        "def score_boom(input, output, expected=None, **kw):\n"
        "    raise RuntimeError(\"boom\")\n",
        "[score_boom]\nmin = 0.5\n")
    rc = _run(games=2, puzzles=4)
    out = capsys.readouterr().out
    assert rc != 0
    assert "error on" in out


def test_no_gate_is_info_row(capsys, team_scorers):
    _install_scorers(
        "def score_latency(input, output, expected=None, **kw):\n"
        "    if \"latency_s\" not in output:\n"
        "        return None\n"
        "    return 1.0 if output[\"latency_s\"] < 3.0 else 0.0\n")
    rc = _run(games=2, puzzles=4)
    out = capsys.readouterr().out
    assert rc != 0  # negative control still applies
    assert "info" in out
    assert "no gate" in out
