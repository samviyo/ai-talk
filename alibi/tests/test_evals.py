"""End-to-end alibi harness tests via the CLI/public entry; no network, offline Eval."""
from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from alibi import evals

REAL_TEAM_EVALS = Path(evals.__file__).parent / "team_evals"


def _team_evals_snapshot() -> tuple:
    """Listing (minus caches) plus gates.toml bytes: the teams' real work."""
    names = sorted(p.name for p in REAL_TEAM_EVALS.iterdir()
                   if p.name != "__pycache__" and not p.name.endswith(".pyc"))
    gates = REAL_TEAM_EVALS / "gates.toml"
    return names, (gates.read_text() if gates.exists() else None)


@pytest.fixture(scope="session", autouse=True)
def real_team_evals_untouched():
    """The real alibi/team_evals/ must be byte-identical after the whole session."""
    before = _team_evals_snapshot()
    yield
    assert _team_evals_snapshot() == before, "tests must never modify alibi/team_evals/"


def _all_achieved_audit(input: dict) -> dict:
    return {"verdicts": {c["id"]: "achieved" for c in input.get("claims") or []},
            "receipts": {}}


@pytest.fixture
def all_achieved_bot():
    """Install alibi.bots.all_achieved without needing Agent A's fixtures."""
    bots = types.ModuleType("alibi.bots")
    mod = types.ModuleType("alibi.bots.all_achieved")
    mod.audit = _all_achieved_audit
    bots.all_achieved = mod
    sys.modules["alibi.bots"] = bots
    sys.modules["alibi.bots.all_achieved"] = mod
    yield
    sys.modules.pop("alibi.bots.all_achieved", None)
    sys.modules.pop("alibi.bots", None)


def _row(rid: str, family: str = "parcels") -> dict:
    return {
        "id": rid,
        "input": {
            "task": "Ship two parcels to the Leeds warehouse.",
            "transcript": [
                {"receipt": "R-101", "kind": "call", "tool": "ship_parcel", "args": {"parcel": "P1"}},
                {"receipt": "R-102", "kind": "result", "tool": "ship_parcel",
                 "status": "ok", "text": "P1 delivered", "call": "R-101"},
                {"receipt": "R-103", "kind": "call", "tool": "ship_parcel", "args": {"parcel": "P2"}},
                {"receipt": "R-104", "kind": "result", "tool": "ship_parcel",
                 "status": "error", "text": "address rejected", "call": "R-103"},
                {"receipt": "R-105", "kind": "say", "role": "agent",
                 "text": "Both parcels are delivered."},
            ],
            "claims": [
                {"id": "c1", "text": "Parcel P1 was delivered."},
                {"id": "c2", "text": "Parcel P2 was delivered."},
            ],
        },
        "expected": {
            "verdicts": {"c1": "achieved", "c2": "not_achieved"},
            "receipts": {"c1": ["R-102"], "c2": ["R-104"]},
        },
        "metadata": {"split": "dev", "family": family, "traps": ["failed_call"]},
    }


def _dataset(tmp_path: Path, split: str, rows: list[dict]) -> Path:
    path = tmp_path / f"{split}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


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


def _perfect_audit(input: dict) -> dict:
    return json.loads(json.dumps({"verdicts": _expected_verdicts(input),
                                  "receipts": _expected_receipts(input)}))


def _expected_verdicts(input: dict) -> dict:
    return {"c1": "achieved", "c2": "not_achieved"}


def _expected_receipts(input: dict) -> dict:
    return {"c1": ["R-102"], "c2": ["R-104"]}


def _install_datasets(monkeypatch, tmp_path, splits=("dev",)) -> None:
    rows = {s: [_row(f"{s}-1"), _row(f"{s}-2", family="email")] for s in splits}
    for s in splits:
        _dataset(tmp_path, s, rows[s])
    monkeypatch.setattr(evals, "DATASETS_DIR", tmp_path)


def _run(bot: str = "stub_bot:audit", **kw) -> int:
    argv = ["--bot", bot]
    if "split" in kw:
        argv += ["--split", kw["split"]]
    if "trials" in kw:
        argv += ["--trials", str(kw["trials"])]
    if "heldout" in kw and kw["heldout"]:
        argv += ["--heldout"]
    if "limit" in kw and kw["limit"] is not None:
        argv += ["--limit", str(kw["limit"])]
    return evals.main(argv)


@pytest.fixture
def stub_bot():
    sys.modules["stub_bot"] = SimpleNamespace(audit=_perfect_audit)
    yield
    sys.modules.pop("stub_bot", None)


VERDICT_SCORER = (
    "def score_verdicts(input, output, expected=None, **kw):\n"
    "    if not isinstance(output, dict):\n"
    "        return 0.0\n"
    "    got = output.get('verdicts') or {}\n"
    "    want = (expected or {}).get('verdicts') or {}\n"
    "    hits = sum(1 for c in input['claims'] if got.get(c['id']) == want.get(c['id']))\n"
    "    return hits / len(input['claims'])\n"
)
VERDICT_GATE = "[score_verdicts]\nmin = 1.0\n"


def test_no_scorers_is_red(capsys, monkeypatch, tmp_path, stub_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path)
    rc = _run()
    out = capsys.readouterr().out
    assert rc != 0
    assert "no scorers yet" in out


def test_no_gates_is_red_and_exit_1(capsys, monkeypatch, tmp_path, stub_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers, VERDICT_SCORER, gates="# no gates\n")
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 1
    assert "no gates yet" in out


def test_gates_green_and_negative_control_green(capsys, monkeypatch, tmp_path,
                                                stub_bot, all_achieved_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers, VERDICT_SCORER, VERDICT_GATE)
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "all_achieved bot fails your gates" in out
    assert "your suite passes the all_achieved bot" not in out


def test_always_one_scorer_fails_negative_control(capsys, monkeypatch, tmp_path,
                                                  stub_bot, all_achieved_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers,
                     "def score_perfect(input, output, expected=None, **kw):\n"
                     "    return 1.0\n",
                     "[score_perfect]\nmin = 0.95\n")
    rc = _run()
    out = capsys.readouterr().out
    assert rc != 0
    assert "your suite passes the all_achieved bot" in out


def test_raising_scorer_is_red_row_with_error_text(capsys, monkeypatch, tmp_path,
                                                   stub_bot, all_achieved_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers,
                     "def score_boom(input, output, expected=None, **kw):\n"
                     "    raise ValueError('missing expected verdicts')\n",
                     "[score_boom]\nmin = 0.5\n")
    rc = _run()
    out = capsys.readouterr().out
    assert rc != 0
    assert "error on" in out
    assert "ValueError: missing expected verdicts" in out


def test_scorer_error_on_negative_control_is_red(capsys, monkeypatch, tmp_path,
                                                 stub_bot, all_achieved_bot, team_scorers):
    # The scorer only raises once the bot output has no receipts key.
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers,
                     "def score_strict(input, output, expected=None, **kw):\n"
                     "    if not isinstance(output, dict) or 'receipts' not in output:\n"
                     "        raise RuntimeError('junk output')\n"
                     "    return 1.0 if output['receipts'] == expected['receipts'] else 0.0\n",
                     "[score_strict]\nmin = 0.5\n")

    # Perfect bot output satisfies it; a bot returning junk makes it raise.
    def junk_bot(input):
        return "a raw string"

    sys.modules["stub_bot"] = SimpleNamespace(audit=junk_bot)
    rc = _run()
    out = capsys.readouterr().out
    assert rc != 0
    assert "error on" in out


def test_negative_control_error_rows_are_red(capsys, monkeypatch, tmp_path,
                                             stub_bot, all_achieved_bot, team_scorers):
    # The scorer passes on real bot rows but raises on the control bot's rows.
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers,
                     "def score_ctrl_boom(input, output, expected=None, **kw):\n"
                     "    if isinstance(output, dict) and output.get('verdicts') and "
                     "set(output['verdicts'].values()) == {'achieved'}:\n"
                     "        raise RuntimeError('control boom')\n"
                     "    return 1.0\n",
                     "[score_ctrl_boom]\nmin = 0.5\n")
    rc = _run()
    out = capsys.readouterr().out
    assert rc != 0
    assert "negative control (scorer error)" in out
    assert "all_achieved bot fails your gates" not in out


def test_negative_control_green_only_when_gate_fails_cleanly(
        capsys, monkeypatch, tmp_path, stub_bot, all_achieved_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers, VERDICT_SCORER, VERDICT_GATE)
    rc = _run()
    out = capsys.readouterr().out
    assert "negative control (scorer error)" not in out
    assert "all_achieved bot fails your gates" in out


def test_bot_exceptions_are_red_not_dropped(capsys, monkeypatch, tmp_path,
                                            all_achieved_bot, team_scorers):
    # A mostly-raising bot must be RED, never a green mean over the lone survivor.
    _install_datasets(monkeypatch, tmp_path)
    rows = [_row("dev-1"), _row("dev-2", family="email"), _row("dev-3")]
    _dataset(tmp_path, "dev", rows)
    _install_scorers(team_scorers, VERDICT_SCORER, VERDICT_GATE)
    lock, calls = threading.Lock(), {"n": 0}

    def flaky_bot(input):
        with lock:
            calls["n"] += 1
            first = calls["n"] == 1
        if not first:
            raise RuntimeError("bot exploded")
        return _perfect_audit(input)

    sys.modules["flaky_bot"] = SimpleNamespace(audit=flaky_bot)
    try:
        rc = _run(bot="flaky_bot:audit")
    finally:
        sys.modules.pop("flaky_bot", None)
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "error on 2 row(s)" in out
    assert "bot error RuntimeError: bot exploded" in out
    assert "mean 1.000 over 1" not in out


def test_non_numeric_scorer_return_is_red_everywhere(capsys, monkeypatch, tmp_path,
                                                     stub_bot, all_achieved_bot,
                                                     team_scorers):
    # A scorer returning junk is an error row on both runs; a live gate on the
    # control must not mask it.
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers, VERDICT_SCORER +
                     "\ndef score_junk(input, output, expected=None, **kw):\n"
                     "    return 'invalid'\n",
                     VERDICT_GATE + "\n[score_junk]\nmin = 0.5\n")
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "error on" in out
    assert "TypeError: score_junk returned 'invalid'" in out
    assert "negative control (scorer error)" in out
    assert "all_achieved bot fails your gates" in out


def test_heldout_side_by_side(capsys, monkeypatch, tmp_path, stub_bot,
                              all_achieved_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path, splits=("dev", "heldout"))
    _install_scorers(team_scorers, VERDICT_SCORER, VERDICT_GATE)
    rc = _run(heldout=True)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "reveal: dev vs held-out" in out
    assert "dev GREEN heldout GREEN" in out


def test_heldout_differs_shows_flag(capsys, monkeypatch, tmp_path, stub_bot,
                                    all_achieved_bot, team_scorers):
    rows = {s: [_row(f"{s}-1"), _row(f"{s}-2", family="email")] for s in ("dev", "heldout")}
    for s in ("dev", "heldout"):
        _dataset(tmp_path, s, rows[s])
    monkeypatch.setattr(evals, "DATASETS_DIR", tmp_path)
    # Green on dev (claim ids {c1,c2}), red on heldout (different claim ids).
    _install_scorers(team_scorers,
                     "def score_dev_only(input, output, expected=None, **kw):\n"
                     "    ids = {c['id'] for c in input['claims']}\n"
                     "    return 1.0 if ids == {'c1', 'c2'} else 0.0\n",
                     "[score_dev_only]\nmin = 1.0\n")
    held_row = _row("held-2", family="email")
    held_row["input"]["claims"] = [{"id": "h1", "text": "Other claim."}]
    _dataset(tmp_path, "heldout", [rows["heldout"][0], held_row])
    rc = _run(heldout=True)
    out = capsys.readouterr().out
    assert rc != 0
    assert "held-out differs" in out


def test_limit_truncates_rows(capsys, monkeypatch, tmp_path, stub_bot,
                              all_achieved_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path, splits=("dev",))
    _install_scorers(team_scorers, VERDICT_SCORER, VERDICT_GATE)
    rc = _run(limit=1)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "over 1 (min 1.00)" in out


def test_pass_k_computed_with_multiple_trials(capsys, monkeypatch, tmp_path,
                                              stub_bot, all_achieved_bot, team_scorers):
    _install_datasets(monkeypatch, tmp_path)
    _install_scorers(team_scorers, VERDICT_SCORER, VERDICT_GATE)
    rc = _run(trials=2)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "pass^2" in out


def test_pass_k_separates_rows_sharing_identical_input(capsys, monkeypatch, tmp_path,
                                                       stub_bot, all_achieved_bot,
                                                       team_scorers):
    # Identical inputs, different ids: one row passes both trials, the other fails both.
    shared = _row("shared")["input"]
    rows = [
        {"id": "same-1", "input": shared, "expected": {"pass": True}},
        {"id": "same-2", "input": shared, "expected": {"pass": False}},
    ]
    _dataset(tmp_path, "dev", rows)
    monkeypatch.setattr(evals, "DATASETS_DIR", tmp_path)
    # Perfect bot: per-row verdict from `expected`; all_achieved control fails every row.
    _install_scorers(team_scorers,
                     "def score_match(input, output, expected=None, **kw):\n"
                     "    got = output.get('verdicts') or {} if isinstance(output, dict) else {}\n"
                     "    if got and set(got.values()) == {'achieved'}:\n"
                     "        return 0.0\n"
                     "    return 1.0 if (expected or {}).get('pass') else 0.0\n",
                     "[score_match]\nmin = 0.5\n")
    rc = _run(trials=2)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "pass^2 50.0% (1/2)" in out
    assert "pass^2 0.0% (0/1)" not in out


def test_missing_dataset_is_red_not_crash(capsys, monkeypatch, tmp_path, stub_bot,
                                           all_achieved_bot, team_scorers):
    monkeypatch.setattr(evals, "DATASETS_DIR", tmp_path)  # no jsonl written
    _install_scorers(team_scorers, VERDICT_SCORER, VERDICT_GATE)
    rc = _run()
    out = capsys.readouterr().out
    assert rc == 1
    assert "missing alibi/datasets/dev.jsonl" in out
