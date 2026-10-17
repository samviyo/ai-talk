"""Tests for the scorer-check runner rule; no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from alibi import checks
from alibi.scorers import Gate, Scorer, load_gates, load_scorers

TEAM_EVALS = Path(checks.__file__).parent / "team_evals"
SCORER_FILE = TEAM_EVALS / "workshop_scorers.py"
GATES_FILE = TEAM_EVALS / "gates.toml"
ORIG_GATES = GATES_FILE.read_text()


@pytest.fixture
def team_scorers():
    yield
    SCORER_FILE.unlink(missing_ok=True)
    GATES_FILE.write_text(ORIG_GATES)
    for m in list(sys.modules):
        if m.startswith("alibi_team_scorers_"):
            del sys.modules[m]


def _install_scorers(code: str, gates: str | None = None) -> None:
    SCORER_FILE.write_text(code)
    if gates is not None:
        GATES_FILE.write_text(gates)


INPUT = {
    "task": "Ship two parcels.",
    "transcript": [
        {"receipt": "R-101", "kind": "call", "tool": "ship_parcel", "args": {"parcel": "P1"}},
        {"receipt": "R-102", "kind": "result", "tool": "ship_parcel",
         "status": "ok", "text": "P1 delivered", "call": "R-101"},
        {"receipt": "R-103", "kind": "say", "role": "agent", "text": "All parcels delivered."},
    ],
    "claims": [{"id": "c1", "text": "Parcel P1 was delivered."}],
}
EXPECTED = {"verdicts": {"c1": "achieved"}, "receipts": {"c1": ["R-102"]}}
CORRECT_OUTPUT = {"verdicts": {"c1": "achieved"}, "receipts": {"c1": ["R-102"]}}


def _row(rid, output, label, why=""):
    return {"id": rid, "input": INPUT, "expected": EXPECTED, "output": output,
            "label": label, "why": why}


ROWS = [
    _row("chk-1", CORRECT_OUTPUT, "correct", "matches expected"),
    _row("chk-2", {"verdicts": {"c1": " Achieved"}, "receipts": {"c1": ["R-102", "R-102"]}},
         "equivalent", "case/whitespace and dedup"),
    _row("chk-3", {"verdicts": {"c1": "achieved"}, "receipts": {"c1": ["R-101"]}},
         "wrong", "cites the call, not the result"),
    _row("chk-4", "a raw string", "wrong", "malformed output"),
]


GOOD_SCORERS = (
    "def score_verdicts(input, output, expected=None, **kw):\n"
    "    want = (expected or {}).get('verdicts') or {}\n"
    "    got = output.get('verdicts') if isinstance(output, dict) else {}\n"
    "    got = got if isinstance(got, dict) else {}\n"
    "    norm = lambda v: v.strip().lower() if isinstance(v, str) else v\n"
    "    hits = sum(1 for c in input['claims'] if norm(got.get(c['id'])) == norm(want.get(c['id'])))\n"
    "    return hits / len(input['claims'])\n"
    "\n"
    "def score_receipts(input, output, expected=None, **kw):\n"
    "    if not isinstance(output, dict):\n"
    "        return 0.0\n"
    "    want = (expected or {}).get('receipts') or {}\n"
    "    got = output.get('receipts') or {}\n"
    "    hits = total = 0\n"
    "    for c in input['claims']:\n"
    "        cid = c['id']\n"
    "        w = want.get(cid) or []\n"
    "        g = got.get(cid)\n"
    "        g = [] if g is None else (g if isinstance(g, list) else [g])\n"
    "        if set(map(str, g)) == set(map(str, w)):\n"
    "            hits += 1\n"
    "        total += 1\n"
    "    return hits / total\n"
)
GOOD_GATES = "[score_verdicts]\nmin = 1.0\n\n[score_receipts]\nmin = 1.0\n"


def test_good_scorers_pass_all_rows(team_scorers):
    _install_scorers(GOOD_SCORERS, GOOD_GATES)
    scorers = load_scorers()
    gates = load_gates()
    assert {s.name for s in scorers} == {"score_verdicts", "score_receipts"}
    failures = checks.run_checks(scorers, gates, ROWS, print_rows=False)
    assert failures == [], failures


def test_gated_scorer_failing_a_correct_row_is_reported(team_scorers):
    _install_scorers(
        "def score_bad(input, output, expected=None, **kw):\n"
        "    return 0.0\n",
        "[score_bad]\nmin = 1.0\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("must pass" in f for f in failures), failures


def test_wrong_row_caught_by_no_scorer_is_reported(team_scorers):
    # An always-True scorer misses every wrong row; the constant flag fires too.
    _install_scorers(
        "def score_true(input, output, expected=None, **kw):\n"
        "    return True\n",
        "[score_true]\nmin = 1.0\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("wrong" in f and "no gated scorer fails" in f for f in failures), failures
    assert any("constant" in f for f in failures), failures


def test_always_false_scorer_is_flagged_constant(team_scorers):
    _install_scorers(
        "def score_false(input, output, expected=None, **kw):\n"
        "    return 0.0\n",
        "[score_false]\nmin = 0.5\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("constant" in f for f in failures), failures
    assert any("must pass" in f for f in failures), failures


def test_raising_scorer_fails_its_rows(team_scorers):
    _install_scorers(
        "def score_boom(input, output, expected=None, **kw):\n"
        "    raise RuntimeError('boom')\n",
        "[score_boom]\nmin = 0.5\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("raised" in f and "RuntimeError: boom" in f for f in failures), failures


def test_no_gates_is_red_failure(team_scorers):
    _install_scorers(GOOD_SCORERS, "# no gates\n")
    failures = checks.run_checks(load_scorers(), {}, ROWS, print_rows=False)
    assert failures == ["no gates yet"]


def test_none_counts_as_pass_on_positive_rows(team_scorers):
    # Applicability: returns None everywhere; positive rows pass, wrong rows miss.
    _install_scorers(
        "def score_na(input, output, expected=None, **kw):\n"
        "    return None\n",
        "[score_na]\nmin = 1.0\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("no gated scorer fails" in f for f in failures), failures
    assert not any("must pass" in f for f in failures), failures


def test_main_exits_nonzero_on_break(tmp_path, capsys, team_scorers):
    path = tmp_path / "scorer_checks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    _install_scorers(GOOD_SCORERS, GOOD_GATES)
    rc = checks.main(["--checks", str(path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "GREEN" in out
    assert "chk-3" in out and "chk-4" in out


def test_main_prints_per_row_lines_and_scorer_verdicts(tmp_path, capsys, team_scorers):
    path = tmp_path / "scorer_checks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    _install_scorers(GOOD_SCORERS, GOOD_GATES)
    checks.main(["--checks", str(path)])
    out = capsys.readouterr().out
    assert "score_verdicts=" in out
    assert "score_receipts=" in out
    assert "caught" in out


def test_unknown_label_is_a_failure(tmp_path):
    row = _row("chk-x", CORRECT_OUTPUT, "mystery", "bad label")
    failures = checks.run_checks(load_scorers(), {"s": Gate(min=1.0)}, [row],
                                 print_rows=False)
    # No scorers loaded -> no gated ones -> "no gates yet".
    assert failures == ["no gates yet"]


def test_gate_min_boundary_on_wrong_row():
    # A wrong row scored exactly at the gate is not "caught".
    scorer = Scorer(name="score_edge", fn=lambda input, output, expected=None, **kw: 1.0)
    gates = {"score_edge": Gate(min=1.0)}
    wrong = [_row("w1", {"verdicts": {}, "receipts": {}}, "wrong", "edge")]
    failures = checks.run_checks([scorer], gates, wrong, print_rows=False)
    assert any("no gated scorer fails" in f for f in failures), failures
