"""Tests for the scorer-check runner rule; no network."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from alibi import checks
from alibi.scorers import Gate, Scorer, load_gates, load_scorers


@pytest.fixture
def team_scorers(tmp_path, monkeypatch):
    """Throwaway team-evals dir via the env override; the real kit is never touched."""
    team_dir = tmp_path / "team_evals"
    team_dir.mkdir()
    monkeypatch.setenv("ALIBI_TEAM_EVALS_DIR", str(team_dir))
    yield team_dir
    for m in list(sys.modules):
        if m.startswith("alibi_team_scorers_"):
            del sys.modules[m]


def _install_scorers(team_dir: Path, code: str, gates: str | None = None) -> None:
    (team_dir / "workshop_scorers.py").write_text(code)
    if gates is not None:
        (team_dir / "gates.toml").write_text(gates)


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
    _install_scorers(team_scorers, GOOD_SCORERS, GOOD_GATES)
    scorers = load_scorers()
    gates = load_gates()
    assert {s.name for s in scorers} == {"score_verdicts", "score_receipts"}
    failures = checks.run_checks(scorers, gates, ROWS, print_rows=False)
    assert failures == [], failures


def test_gated_scorer_failing_a_correct_row_is_reported(team_scorers):
    _install_scorers(
        team_scorers,
        "def score_bad(input, output, expected=None, **kw):\n"
        "    return 0.0\n",
        "[score_bad]\nmin = 1.0\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("must pass" in f for f in failures), failures


def test_wrong_row_caught_by_no_scorer_is_reported(team_scorers):
    # An always-True scorer misses every wrong row; the constant flag fires too.
    _install_scorers(
        team_scorers,
        "def score_true(input, output, expected=None, **kw):\n"
        "    return True\n",
        "[score_true]\nmin = 1.0\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("wrong" in f and "no gated scorer fails" in f for f in failures), failures
    assert any("constant" in f for f in failures), failures


def test_always_false_scorer_is_flagged_constant(team_scorers):
    _install_scorers(
        team_scorers,
        "def score_false(input, output, expected=None, **kw):\n"
        "    return 0.0\n",
        "[score_false]\nmin = 0.5\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("constant" in f for f in failures), failures
    assert any("must pass" in f for f in failures), failures


def test_raising_scorer_fails_its_rows(team_scorers):
    _install_scorers(
        team_scorers,
        "def score_boom(input, output, expected=None, **kw):\n"
        "    raise RuntimeError('boom')\n",
        "[score_boom]\nmin = 0.5\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("raised" in f and "RuntimeError: boom" in f for f in failures), failures


def test_no_gates_is_red_failure(team_scorers):
    _install_scorers(team_scorers, GOOD_SCORERS, "# no gates\n")
    failures = checks.run_checks(load_scorers(), {}, ROWS, print_rows=False)
    assert failures == ["no gates yet"]


def test_none_counts_as_pass_on_positive_rows(team_scorers):
    # Applicability: returns None everywhere; positive rows pass, wrong rows miss.
    _install_scorers(
        team_scorers,
        "def score_na(input, output, expected=None, **kw):\n"
        "    return None\n",
        "[score_na]\nmin = 1.0\n")
    failures = checks.run_checks(load_scorers(), load_gates(), ROWS, print_rows=False)
    assert any("no gated scorer fails" in f for f in failures), failures
    assert not any("must pass" in f for f in failures), failures


def test_main_exits_nonzero_on_break(tmp_path, capsys, team_scorers):
    path = tmp_path / "scorer_checks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    _install_scorers(team_scorers, GOOD_SCORERS, GOOD_GATES)
    rc = checks.main(["--checks", str(path)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "GREEN" in out
    assert "chk-3" in out and "chk-4" in out


def test_main_prints_per_row_lines_and_scorer_verdicts(tmp_path, capsys, team_scorers):
    path = tmp_path / "scorer_checks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    _install_scorers(team_scorers, GOOD_SCORERS, GOOD_GATES)
    checks.main(["--checks", str(path)])
    out = capsys.readouterr().out
    assert "score_verdicts=" in out
    assert "score_receipts=" in out
    assert "caught" in out


def _const(value):
    return lambda input, output, expected=None, **kw: value


def test_unknown_label_is_a_failure():
    row = _row("chk-x", CORRECT_OUTPUT, "mystery", "bad label")
    scorer = Scorer(name="score_s", fn=_const(1.0))
    result = checks.evaluate([scorer], {"score_s": Gate(min=1.0)}, [row])
    assert result["rows"][0]["ok"] is False
    assert [f["kind"] for f in result["failures"]] == ["unknown_label", "constant"]


def test_evaluate_runs_only_gated_scorers_in_load_order():
    calls = []

    def spy(name):
        def fn(input, output, expected=None, **kw):
            calls.append(name)
            return 1.0
        return fn

    scorers = [Scorer("score_a", spy("a")), Scorer("score_b", spy("b")), Scorer("score_c", spy("c"))]
    gates = {"score_c": Gate(min=0.5), "score_a": Gate(min=0.5), "score_missing": Gate(min=0.5)}
    result = checks.evaluate(scorers, gates, ROWS[:1])
    assert result["gated"] == ["score_a", "score_c"]
    assert calls == ["a", "c"]


def test_evaluate_records_none_nan_and_values_per_row():
    def picky(input, output, expected=None, **kw):
        if output == "a raw string":
            raise KeyError("verdicts")
        return None if output.get("receipts", {}).get("c1") == ["R-101"] else 0.25

    gates = {"score_p": Gate(min=0.2)}
    result = checks.evaluate([Scorer("score_p", picky)], gates, ROWS)
    values = result["values"]["score_p"]
    assert values["chk-1"] == 0.25 and values["chk-3"] is None
    assert math.isnan(values["chk-4"])
    kinds = [(f["kind"], f["row"], f["scorer"]) for f in result["failures"]]
    assert kinds == [("slips_through", "chk-3", None),
                     ("raised", "chk-4", "score_p", ),
                     ("slips_through", "chk-4", None)]
    assert "KeyError" in result["failures"][1]["text"]
    assert [r["ok"] for r in result["rows"]] == [True, True, False, False]


def test_evaluate_one_catching_scorer_is_enough_on_a_wrong_row():
    scorers = [Scorer("score_hi", _const(1.0)),
               Scorer("score_lo", lambda input, output, expected=None, **kw:
                      0.0 if output == "a raw string" else 1.0)]
    gates = {"score_hi": Gate(min=1.0), "score_lo": Gate(min=1.0)}
    result = checks.evaluate(scorers, gates, ROWS)
    by_id = {r["id"]: r for r in result["rows"]}
    assert by_id["chk-4"]["ok"] is True and by_id["chk-4"]["caught"] == ["score_lo"]
    assert by_id["chk-3"]["ok"] is False
    assert ("constant", "score_hi") in [(f["kind"], f["scorer"]) for f in result["failures"]]
    assert ("constant", "score_lo") not in [(f["kind"], f["scorer"]) for f in result["failures"]]


def test_none_everywhere_is_not_constant():
    result = checks.evaluate([Scorer("score_n", _const(None))], {"score_n": Gate(min=1.0)}, ROWS)
    assert "constant" not in [f["kind"] for f in result["failures"]]


def test_bool_returns_count_as_one_and_zero():
    scorer = Scorer("score_b", lambda input, output, expected=None, **kw: isinstance(output, dict))
    result = checks.evaluate([scorer], {"score_b": Gate(min=1.0)}, ROWS)
    assert result["values"]["score_b"] == {"chk-1": 1.0, "chk-2": 1.0, "chk-3": 1.0, "chk-4": 0.0}


def test_run_checks_text_matches_evaluate_failures():
    scorer = Scorer("score_x", _const(0.5))
    gates = {"score_x": Gate(min=0.8)}
    texts = checks.run_checks([scorer], gates, ROWS, print_rows=False)
    assert texts == [f["text"] for f in checks.evaluate([scorer], gates, ROWS)["failures"]]
    assert "must pass (got 0.50, min 0.80)" in texts[0]


def test_gate_min_boundary_on_wrong_row():
    # A wrong row scored exactly at the gate is not "caught".
    scorer = Scorer(name="score_edge", fn=lambda input, output, expected=None, **kw: 1.0)
    gates = {"score_edge": Gate(min=1.0)}
    wrong = [_row("w1", {"verdicts": {}, "receipts": {}}, "wrong", "edge")]
    failures = checks.run_checks([scorer], gates, wrong, print_rows=False)
    assert any("no gated scorer fails" in f for f in failures), failures


NOTES_PATH = (Path(__file__).resolve().parent.parent / "datasets").parent.parent \
    / "reference" / "alibi" / "scorer_check_notes.jsonl"


def _note_whys():
    return [
        json.loads(line)["why"]
        for line in NOTES_PATH.read_text().splitlines()
        if line
    ]


def test_check_output_never_prints_note_texts(team_scorers, capsys):
    _install_scorers(team_scorers, GOOD_SCORERS, GOOD_GATES)
    rows = checks.load_check_rows(checks.CHECKS_PATH)
    checks.run_checks(load_scorers(), load_gates(), rows, print_rows=True)
    out = capsys.readouterr().out
    assert "sc-001" in out  # rows were actually printed
    for why in _note_whys():
        assert why not in out, why
