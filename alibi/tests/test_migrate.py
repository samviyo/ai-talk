"""Tests for the migration tooling: case generator, Python stages, differ, golden, parity runner."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from alibi import checks
from alibi.migrate import cases, reference
from alibi.migrate import __main__ as migrate
from alibi.scorers import Gate, load_scorers

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_EVALS = ROOT / "reference" / "alibi"


# -- case generator ------------------------------------------------------------

def test_generate_is_deterministic_and_seed_sensitive():
    assert cases.generate(7) == cases.generate(7)
    assert cases.generate(7) != cases.generate(8)


def test_generate_covers_every_mutation_with_valid_rows():
    rows = cases.generate(3)
    assert len(rows) == cases.CASES_PER_SET
    assert len({r["id"] for r in rows}) == len(rows)
    assert {r["label"] for r in rows} == {"correct", "equivalent", "wrong"}
    assert sum(r["label"] == "correct" for r in rows) >= 1
    for r in rows:
        assert set(r) == {"id", "input", "expected", "output", "label"}
        if r["label"] == "correct":
            assert r["output"] == r["expected"]


def test_heldout_and_practice_use_their_own_transcripts():
    def receipts(rows):
        return {e["receipt"][:2] for r in rows for e in r["input"]["transcript"]}
    assert receipts(cases.generate(5)) == {"H-"}
    assert receipts(cases.generate(5, split="dev")) == {"R-"}


def _all_unknown(row):
    return all(v == "unknown" for v in row["expected"]["verdicts"].values())


@pytest.mark.parametrize("seed", range(1, 16))
def test_labels_agree_with_the_reference_scorers(seed):
    # The organiser scorers are a sound auditor check, so generated labels must match them.
    scorers = {s.name: s.fn for s in load_scorers(str(REFERENCE_EVALS))}
    for row in cases.generate(seed):
        if _all_unknown(row):
            continue  # score_receipts_correct returns 0.0 when no claim is gradable
        perfect = all(fn(input=row["input"], output=row["output"], expected=row["expected"]) >= 1.0
                      for fn in scorers.values())
        assert perfect == (row["label"] != "wrong"), (row["id"], row["output"])


def test_generated_rows_never_crash_the_reference_scorers():
    rows = [r for seed in range(1, 30) for r in cases.generate(seed)]
    result = checks.evaluate(load_scorers(str(REFERENCE_EVALS)),
                             {"score_verdicts_correct": Gate(1.0),
                              "score_receipts_correct": Gate(1.0)}, rows)
    assert "raised" not in {f["kind"] for f in result["failures"]}


# -- Python reference stages ---------------------------------------------------------------

SCORERS_SRC = '''
def score_shape(input, output, expected=None, **kw):
    if output is None:
        return None
    return output["verdicts"] == expected["verdicts"]
'''


@pytest.fixture
def case_dir(tmp_path):
    d = tmp_path / "case"
    d.mkdir()
    rows = [
        {"id": "a", "label": "correct", "input": {"claims": []},
         "expected": {"verdicts": {"c1": "achieved"}}, "output": {"verdicts": {"c1": "achieved"}}},
        {"id": "b", "label": "wrong", "input": {"claims": []},
         "expected": {"verdicts": {"c1": "achieved"}}, "output": "junk", "why": "dropped"},
        {"id": "c", "label": "wrong", "input": {"claims": []}, "output": None},
    ]
    (d / "rows.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    (d / "gates.toml").write_text("[score_shape]\nmin = 1\n\n[not_a_gate]\nnote = 'x'\n")
    (d / "evals.py").write_text(SCORERS_SRC)
    return d


def _stages(case_dir):
    return reference.stages(case_dir / "rows.jsonl", case_dir, load_scorers(str(case_dir)))


def test_parse_is_canonical(case_dir):
    parsed = _stages(case_dir)["parse"]
    assert parsed["gates"] == {"score_shape": {"min": 1.0, "min_n": 1}}
    assert isinstance(parsed["gates"]["score_shape"]["min"], float)
    assert [list(r) for r in parsed["rows"]] == [list(reference.ROW_KEYS)] * 3
    assert parsed["rows"][2]["expected"] is None


def test_score_encodes_errors_and_not_applicable(case_dir):
    score = _stages(case_dir)["score"]
    assert score == {"gated": ["score_shape"],
                     "values": {"score_shape": {"a": 1.0, "b": "error", "c": None}}}


def test_report_and_run_agree_with_the_tool(case_dir):
    out = _stages(case_dir)
    report = out["report"]
    assert report["rows"] == [{"id": "a", "label": "correct", "ok": True},
                              {"id": "b", "label": "wrong", "ok": False},
                              {"id": "c", "label": "wrong", "ok": False}]
    assert report["failures"] == [{"kind": "raised", "row": "b", "scorer": "score_shape"},
                                  {"kind": "slips_through", "row": "b", "scorer": None},
                                  {"kind": "slips_through", "row": "c", "scorer": None}]
    assert report["green"] is False
    assert out["run"] == {**out["score"], **out["report"]}
    rows = checks.load_check_rows(case_dir / "rows.jsonl")
    texts = checks.run_checks(load_scorers(str(case_dir)), {"score_shape": Gate(1.0)}, rows,
                              print_rows=False)
    assert len(texts) == len(report["failures"])


def test_scorer_mutation_does_not_leak_into_golden_parse(case_dir):
    (case_dir / "evals.py").write_text(SCORERS_SRC.replace(
        'return output["verdicts"]', 'return output.pop("verdicts")'))
    out = _stages(case_dir)
    assert out["parse"]["rows"][0]["output"] == {"verdicts": {"c1": "achieved"}}


def test_stages_with_no_gates(case_dir):
    (case_dir / "gates.toml").write_text("# nothing gated\n")
    out = _stages(case_dir)
    assert out["score"] == {"gated": [], "values": {}}
    assert out["report"] == {"green": False, "rows": [],
                             "failures": [{"kind": "no_gates", "row": None, "scorer": None}]}


# -- differ ------------------------------------------------------------------------

@pytest.mark.parametrize("want,got,n", [
    ({"v": 0.1 + 0.2}, {"v": 0.3}, 0),
    ({"v": 1.0}, {"v": 1}, 0),
    ({"v": 0.5}, {"v": 0.5001}, 1),
    ({"ok": True}, {"ok": 1}, 1),
    ({"v": None}, {"v": 0}, 1),
    ({"v": "error"}, {"v": 0.0}, 1),
    ({"rows": [1, 2]}, {"rows": [2, 1]}, 2),
    ({"rows": [1, 2]}, {"rows": [1]}, 1),
    ({"a": 1}, {}, 1),
    ({}, {"a": 1}, 1),
    ({"v": float("inf")}, {"v": float("inf")}, 0),
])
def test_diff(want, got, n):
    assert len(migrate.diff(want, got)) == n


def test_diff_ignores_failure_order_but_not_failure_content():
    a = {"kind": "raised", "row": "x", "scorer": "s"}
    b = {"kind": "constant", "row": None, "scorer": "s"}
    assert migrate.diff({"failures": [a, b]}, {"failures": [b, a]}) == []
    assert migrate.diff({"failures": [a, b]}, {"failures": [a, a]}) != []


def test_diff_names_the_path():
    want = {"values": {"s": {"r1": 1.0}}}
    assert migrate.diff(want, {"values": {"s": {"r1": 0.0}}}) == ["$.values.s.r1: want 1.0, got 0.0"]


# -- golden ------------------------------------------------------------------------

def _writable(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        p.chmod(stat.S_IMODE(p.stat().st_mode) | stat.S_IWUSR)


@pytest.fixture
def locked_evals(tmp_path):
    d = tmp_path / "team_evals"
    d.mkdir()
    for name in ("organiser_evals.py", "gates.toml"):
        (d / name).write_text((REFERENCE_EVALS / name).read_text())
    (d / ".locked").touch()
    return d


@pytest.fixture
def golden_out(tmp_path):
    out = tmp_path / "golden"
    yield out
    if out.exists():
        _writable(out)


def test_golden_refuses_unlocked_scorers(locked_evals, golden_out, capsys):
    (locked_evals / ".locked").unlink()
    assert migrate.golden(golden_out, locked_evals, require_lock=True) == 1
    assert "make lock-alibi" in capsys.readouterr().out
    assert not golden_out.exists()


def test_golden_freezes_every_stage_and_locks(locked_evals, golden_out):
    assert migrate.golden(golden_out, locked_evals, require_lock=True) == 0
    for case in ("checks", "practice"):
        for name in ("rows.jsonl", "gates.toml", *(f"{s}.json" for s in migrate.STAGES)):
            path = golden_out / case / name
            assert path.exists(), path
            assert not os.access(path, os.W_OK), path
    assert (golden_out / ".locked").exists()
    want = reference.stages(golden_out / "checks" / "rows.jsonl", golden_out / "checks",
                            load_scorers(str(locked_evals)))
    assert json.loads((golden_out / "checks" / "run.json").read_text()) == want["run"]
    # A second run must not overwrite frozen answers.
    assert migrate.golden(golden_out, locked_evals, require_lock=True) == 1


# -- parity and reveal through stand-in ports ----------------------------------------

PYTHON_PORT = '''#!{python}
"""Stand-in port: the Python tool itself, with an optional sabotage."""
import json, sys
from pathlib import Path
sys.path.insert(0, {root!r})
from alibi.migrate import reference
from alibi.scorers import load_scorers
cmd, rows, gates = sys.argv[1:4]
out = reference.stages(Path(rows), Path(gates).parent, load_scorers({evals!r}))["run"]
{sabotage}
print(json.dumps(out))
'''


def _port(tmp_path, evals, sabotage="", name="port"):
    path = tmp_path / name
    path.write_text(PYTHON_PORT.format(python=sys.executable, root=str(ROOT),
                                       evals=str(evals), sabotage=sabotage))
    path.chmod(0o755)
    return path


@pytest.fixture
def golden_dir(locked_evals, golden_out):
    migrate.golden(golden_out, locked_evals, require_lock=True)
    return golden_out


def test_parity_green_for_a_faithful_port(tmp_path, locked_evals, golden_dir):
    port = _port(tmp_path, locked_evals)
    assert migrate.parity(port, golden_dir, ["run"]) == 0
    assert migrate.reveal(port, 4242, locked_evals) == 0


def test_parity_red_when_one_value_drifts(tmp_path, locked_evals, golden_dir, capsys):
    sabotage = ("s = out['gated'][0]; r = next(iter(out['values'][s]));"
                " out['values'][s][r] = 0.123")
    port = _port(tmp_path, locked_evals, sabotage)
    assert migrate.parity(port, golden_dir, ["run"]) == 1
    assert "want" in capsys.readouterr().out


@pytest.mark.parametrize("sabotage,message", [
    ("sys.exit(3)", "exit 3"),
    ("print('not json'); sys.exit(0)", "not JSON"),
])
def test_parity_reports_broken_ports(tmp_path, locked_evals, golden_dir, capsys, sabotage, message):
    port = _port(tmp_path, locked_evals, sabotage)
    assert migrate.parity(port, golden_dir, ["run"]) == 1
    assert message in capsys.readouterr().out


def test_missing_binary_is_red(tmp_path, golden_dir, capsys):
    assert migrate.parity(tmp_path / "nope", golden_dir, ["run"]) == 1
    assert "make zig-build" in capsys.readouterr().out


def test_lookup_port_passes_parity_and_fails_reveal(tmp_path, locked_evals, golden_dir):
    table = {}
    for case in ("checks", "practice"):
        rows = (golden_dir / case / "rows.jsonl").read_text()
        table[rows] = json.loads((golden_dir / case / "run.json").read_text())
    (tmp_path / "table.json").write_text(json.dumps(table))
    sabotage = (f"table = json.loads(Path({str(tmp_path / 'table.json')!r}).read_text());"
                " out = table.get(Path(rows).read_text(), "
                "{'gated': [], 'values': {}, 'green': True, 'rows': [], 'failures': []})")
    port = _port(tmp_path, locked_evals, sabotage)
    assert migrate.parity(port, golden_dir, ["run"]) == 0
    assert migrate.reveal(port, 4242, locked_evals) == 1


def test_reveal_hides_the_expected_answer_from_the_port(tmp_path, locked_evals):
    sabotage = "out = json.loads(Path(rows).with_name('run.json').read_text())"
    port = _port(tmp_path, locked_evals, sabotage)
    assert migrate.reveal(port, 4242, locked_evals) == 1


def test_stage_args_feed_golden_upstream_outputs(tmp_path):
    assert migrate.stage_args("score", tmp_path) == ["score", str(tmp_path / "parse.json")]
    assert migrate.stage_args("report", tmp_path)[1:] == [str(tmp_path / "parse.json"),
                                                           str(tmp_path / "score.json")]


def test_shipped_zig_scorers_match_the_reference_port():
    # The reference Zig port implements reference/alibi's scorers, so the shipped copy must not drift.
    root = Path(__file__).resolve().parents[2]
    shipped = (root / "alibi/zig/scorers/scorers.py").read_text().split("\n", 1)[1]
    reference = (root / "reference/alibi/organiser_evals.py").read_text().split("\n", 1)[1]
    assert shipped == reference
    assert (root / "alibi/zig/scorers/.locked").exists()
