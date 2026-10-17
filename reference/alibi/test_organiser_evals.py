"""Reference-suite tests: scorer behaviour plus the scorer-check rule, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from alibi import checks
from alibi.scorers import load_gates, load_scorers
from reference.alibi import organiser_evals as ref

REF_DIR = Path(ref.__file__).parent


@pytest.fixture(scope="module")
def suite():
    return load_scorers(str(REF_DIR)), load_gates(str(REF_DIR))


INPUT = {
    "task": "Ship three parcels and email the customer.",
    "transcript": [
        {"receipt": "R-101", "kind": "call", "tool": "ship_parcel", "args": {"parcel": "P1"}},
        {"receipt": "R-102", "kind": "result", "tool": "ship_parcel",
         "status": "ok", "text": "P1 delivered", "call": "R-101"},
        {"receipt": "R-103", "kind": "call", "tool": "ship_parcel", "args": {"parcel": "P2"}},
        {"receipt": "R-104", "kind": "result", "tool": "ship_parcel",
         "status": "error", "text": "address rejected", "call": "R-103"},
        {"receipt": "R-105", "kind": "say", "role": "agent", "text": "All three delivered."},
    ],
    "claims": [
        {"id": "c1", "text": "Parcel P1 was delivered."},
        {"id": "c2", "text": "Parcel P2 was delivered."},
        {"id": "c3", "text": "The customer was emailed."},
    ],
}
EXPECTED = {
    "verdicts": {"c1": "achieved", "c2": "not_achieved", "c3": "unknown"},
    "receipts": {"c1": ["R-102"], "c2": ["R-104"], "c3": []},
}


def test_suite_loads_both_gated_scorers(suite):
    scorers, gates = suite
    assert {s.name for s in scorers} >= {"score_verdicts_correct", "score_receipts_correct"}
    assert gates["score_verdicts_correct"].min == 1.0
    assert gates["score_receipts_correct"].min == 1.0


def test_perfect_output_scores_one(suite):
    scorers, _ = suite
    by_name = {s.name: s.fn for s in scorers}
    output = {"verdicts": {"c1": "achieved", "c2": "not_achieved", "c3": "unknown"},
              "receipts": {"c1": ["R-102"], "c2": ["R-104"], "c3": []}}
    assert by_name["score_verdicts_correct"](input=INPUT, output=output, expected=EXPECTED) == 1.0
    assert by_name["score_receipts_correct"](input=INPUT, output=output, expected=EXPECTED) == 1.0


def test_verdict_normalisation(suite):
    scorers, _ = suite
    fn = {s.name: s.fn for s in scorers}["score_verdicts_correct"]
    output = {"verdicts": {"c1": " Achieved ", "c2": "NOT_ACHIEVED", "c3": "Unknown"},
              "receipts": {}}
    assert fn(input=INPUT, output=output, expected=EXPECTED) == 1.0


def test_receipts_set_equality_ignores_order_and_dups(suite):
    scorers, _ = suite
    fn = {s.name: s.fn for s in scorers}["score_receipts_correct"]
    output = {"verdicts": EXPECTED["verdicts"],
              "receipts": {"c1": ["R-102", "R-102"], "c2": ["R-104"], "c3": []}}
    assert fn(input=INPUT, output=output, expected=EXPECTED) == 1.0


def test_receipts_ignores_unknown_claims(suite):
    scorers, _ = suite
    fn = {s.name: s.fn for s in scorers}["score_receipts_correct"]
    # c3 is unknown: its receipts (absent key vs empty list) are not scored.
    output = {"verdicts": EXPECTED["verdicts"],
              "receipts": {"c1": ["R-102"], "c2": ["R-104"]}}
    assert fn(input=INPUT, output=output, expected=EXPECTED) == 1.0


def test_malformed_output_scores_zero_not_none(suite):
    scorers, _ = suite
    by_name = {s.name: s.fn for s in scorers}
    for bad in ("a raw string", None, 42, {"verdicts": "nope"}, {}):
        v = by_name["score_verdicts_correct"](input=INPUT, output=bad, expected=EXPECTED)
        r = by_name["score_receipts_correct"](input=INPUT, output=bad, expected=EXPECTED)
        assert v == 0.0, bad
        assert r == 0.0, bad


def test_wrong_receipt_scores_below_one(suite):
    scorers, _ = suite
    fn = {s.name: s.fn for s in scorers}["score_receipts_correct"]
    output = {"verdicts": EXPECTED["verdicts"], "receipts": {"c1": ["R-101"], "c2": ["R-104"]}}
    assert fn(input=INPUT, output=output, expected=EXPECTED) < 1.0


def _row(rid, output, label, why=""):
    return {"id": rid, "input": INPUT, "expected": EXPECTED, "output": output,
            "label": label, "why": why}


ROWS = [
    _row("ref-1", {"verdicts": EXPECTED["verdicts"], "receipts": EXPECTED["receipts"]},
         "correct", "matches expected"),
    _row("ref-2", {"verdicts": {"c1": " Achieved", "c2": "not_achieved", "c3": "Unknown"},
                   "receipts": {"c1": ["R-102", "R-102"], "c2": ["R-104"]}},
         "equivalent", "case/whitespace, dupes, absent unknown key"),
    _row("ref-3", {"verdicts": {"c1": "achieved", "c2": "achieved", "c3": "achieved"},
                   "receipts": {"c1": ["R-102"], "c2": ["R-101"], "c3": ["R-105"]}},
         "wrong", "trusts the say, cites the call not the result"),
    _row("ref-4", "a raw string", "wrong", "malformed output"),
    _row("ref-5", {"verdicts": {"c1": "not_achieved", "c2": "not_achieved", "c3": "not_achieved"},
                   "receipts": {"c1": [], "c2": ["R-104"], "c3": []}},
         "wrong", "marks everything not_achieved"),
]


def test_reference_suite_passes_the_scorer_check_rule(capsys):
    failures = checks.run_checks(load_scorers(str(REF_DIR)), load_gates(str(REF_DIR)),
                                 ROWS, print_rows=True)
    assert failures == [], failures


def test_reference_suite_passes_via_the_checks_cli(tmp_path, capsys):
    path = tmp_path / "scorer_checks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    rc = checks.main(["--checks", str(path), "--team-evals-dir", str(REF_DIR)])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "GREEN" in out


def test_always_true_scorer_fails_the_check_runner(tmp_path):
    bad = tmp_path / "bad_suite"
    bad.mkdir()
    (bad / "always.py").write_text(
        "def score_yes(input, output, expected=None, **kw):\n    return True\n")
    (bad / "gates.toml").write_text("[score_yes]\nmin = 1.0\n")
    rc = checks.main(["--checks", str(_checks_file(tmp_path)),
                      "--team-evals-dir", str(bad)])
    assert rc != 0


def test_always_false_scorer_fails_the_check_runner(tmp_path):
    bad = tmp_path / "bad_suite"
    bad.mkdir()
    (bad / "always.py").write_text(
        "def score_no(input, output, expected=None, **kw):\n    return False\n")
    (bad / "gates.toml").write_text("[score_no]\nmin = 1.0\n")
    rc = checks.main(["--checks", str(_checks_file(tmp_path)),
                      "--team-evals-dir", str(bad)])
    assert rc != 0


def _checks_file(tmp_path: Path) -> Path:
    path = tmp_path / "scorer_checks.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in ROWS) + "\n")
    return path


def test_always_true_scorer_failure_names_the_reason(tmp_path, capsys):
    bad = tmp_path / "yes_suite"
    bad.mkdir()
    (bad / "always.py").write_text(
        "def score_yes(input, output, expected=None, **kw):\n    return True\n")
    (bad / "gates.toml").write_text("[score_yes]\nmin = 1.0\n")
    checks.main(["--checks", str(_checks_file(tmp_path)), "--team-evals-dir", str(bad)])
    out = capsys.readouterr().out
    assert "no gated scorer fails" in out
    assert "constant" in out
