"""Offline tests for the fixtures: determinism, invariants, labels, disjointness."""

from __future__ import annotations

import json
import sys
from collections import Counter
from types import SimpleNamespace

import pytest

from alibi import evals, fixtures
from alibi.baseline import run
from alibi.bots import all_achieved, all_not_achieved, oracle

LABELS = ("achieved", "not_achieved", "unknown")
BOUNDS = {"achieved": (0.35, 0.45), "not_achieved": (0.30, 0.40), "unknown": (0.20, 0.30)}
HELDOUT_SEED = 4242
REF_DIR = str(fixtures.DATASETS_DIR.parent.parent / "reference" / "alibi")


def _split_rows(split):
    return fixtures.heldout_rows(HELDOUT_SEED) if split == "heldout" else fixtures.load_rows(split)


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory):
    out = tmp_path_factory.mktemp("alibi_datasets")
    fixtures.regenerate(out)
    return out


@pytest.mark.parametrize("split", ("dev",))
def test_regeneration_is_deterministic(tmp_path, split):
    fixtures.regenerate(tmp_path)
    first = (tmp_path / f"{split}.jsonl").read_text()
    fixtures.regenerate(tmp_path)
    second = (tmp_path / f"{split}.jsonl").read_text()
    assert first == second
    # and byte-identical to what is committed in the repo
    committed = (fixtures.DATASETS_DIR / f"{split}.jsonl").read_text()
    assert first == committed


def test_regenerate_writes_no_heldout_file(tmp_path):
    fixtures.regenerate(tmp_path)
    assert not (tmp_path / "heldout.jsonl").exists()


def test_heldout_rows_are_seed_deterministic_and_vary():
    first = fixtures.heldout_rows(HELDOUT_SEED)
    again = fixtures.heldout_rows(HELDOUT_SEED)
    other = fixtures.heldout_rows(HELDOUT_SEED + 1)
    assert first == again
    assert first != other
    assert len(first) == fixtures.ROWS_PER_SPLIT


def test_heldout_rows_use_h_receipt_scheme():
    for row in fixtures.heldout_rows(HELDOUT_SEED):
        assert row["metadata"]["split"] == "heldout"
        for e in row["input"]["transcript"]:
            assert e["receipt"].startswith("H-"), e["receipt"]


def test_heldout_rows_cover_every_kind():
    for row in fixtures.heldout_rows(HELDOUT_SEED):
        for claim in row["input"]["claims"]:
            cid = claim["id"]
            assert row["expected"]["verdicts"][cid] in LABELS
            assert row["expected"]["receipts"][cid] == sorted(row["expected"]["receipts"][cid])


def test_heldout_seed_env_required(monkeypatch):
    monkeypatch.delenv(fixtures.HELDOUT_SEED_ENV, raising=False)
    with pytest.raises(RuntimeError, match=fixtures.HELDOUT_SEED_ENV):
        fixtures.heldout_seed()


def test_heldout_seed_env_reads_integer(monkeypatch):
    monkeypatch.setenv(fixtures.HELDOUT_SEED_ENV, "7")
    assert fixtures.heldout_seed() == 7


def test_scorer_checks_regenerate_identically(tmp_path):
    fixtures.regenerate(tmp_path)
    assert (tmp_path / "scorer_checks.jsonl").read_text() == (
        fixtures.DATASETS_DIR / "scorer_checks.jsonl"
    ).read_text()
    assert not (tmp_path / "scorer_check_notes.jsonl").exists()


def test_scorer_checks_have_no_why_and_notes_cover_every_id():
    checks = [
        json.loads(l)
        for l in (fixtures.DATASETS_DIR / "scorer_checks.jsonl").read_text().splitlines()
        if l
    ]
    for c in checks:
        assert "why" not in c, c["id"]
    notes_path = (
        fixtures.DATASETS_DIR.parent.parent / "reference" / "alibi"
        / "scorer_check_notes.jsonl"
    )
    notes = [json.loads(l) for l in notes_path.read_text().splitlines() if l]
    for n in notes:
        assert set(n) == {"id", "why"}, n
    assert len(notes) == len({n["id"] for n in notes})  # exactly one note per id
    assert {c["id"] for c in checks} == {n["id"] for n in notes}


def test_note_texts_never_appear_under_alibi():
    notes_path = (
        fixtures.DATASETS_DIR.parent.parent / "reference" / "alibi"
        / "scorer_check_notes.jsonl"
    )
    whys = [
        json.loads(l)["why"]
        for l in notes_path.read_text().splitlines()
        if l
    ]
    assert whys
    alibi_root = fixtures.DATASETS_DIR.parent
    for path in alibi_root.rglob("*"):
        if path.is_file():
            # bytes + errors=ignore so compiled .pyc artifacts are scanned too
            text = path.read_bytes().decode("utf-8", errors="ignore")
            for why in whys:
                assert why not in text, (path, why)


def test_equivalent_check_rows_differ_from_expected():
    checks = [
        json.loads(l)
        for l in (fixtures.DATASETS_DIR / "scorer_checks.jsonl").read_text().splitlines()
        if l
    ]
    for c in checks:
        if c["label"] == "equivalent":
            assert c["output"] != c["expected"], (c["id"],)


def test_scorer_checks_cover_every_kind():
    checks = [
        json.loads(l)
        for l in (fixtures.DATASETS_DIR / "scorer_checks.jsonl").read_text().splitlines()
        if l
    ]
    labels = Counter(c["label"] for c in checks)
    assert labels["correct"] >= 1
    assert labels["equivalent"] >= 4
    assert labels["wrong"] >= 7
    for c in checks:
        assert set(c) >= {"id", "input", "expected", "output", "label"}
        # the canned row must be a real dev row
        assert any(
            row["id"] == c["id"] or row["input"] == c["input"] for row in fixtures.load_rows("dev")
        )


@pytest.mark.parametrize("split", fixtures.SPLITS)
def test_row_counts_and_shape(regenerated, split):
    rows = _split_rows(split)
    assert len(rows) == fixtures.ROWS_PER_SPLIT
    assert {r["metadata"]["split"] for r in rows} == {split}
    assert len({r["id"] for r in rows}) == len(rows)
    for row in rows:
        for key in ("id", "input", "expected", "metadata"):
            assert key in row
        for claim in row["input"]["claims"]:
            cid = claim["id"]
            assert row["expected"]["verdicts"][cid] in LABELS
            assert row["expected"]["receipts"][cid] == sorted(row["expected"]["receipts"][cid])


@pytest.mark.parametrize("split", fixtures.SPLITS)
def test_expected_receipts_exist_and_are_results(regenerated, split):
    for row in _split_rows(split):
        results = {
            e["receipt"]: e for e in row["input"]["transcript"] if e.get("kind") == "result"
        }
        for cid, recs in row["expected"]["receipts"].items():
            if row["expected"]["verdicts"][cid] == "unknown":
                assert recs == []
            for rid in recs:
                assert rid in results, (row["id"], cid, rid)
        # minimal: no receipt cited that proves nothing (one receipt minimum otherwise)
        for cid, recs in row["expected"]["receipts"].items():
            if row["expected"]["verdicts"][cid] != "unknown":
                assert recs


@pytest.mark.parametrize("split", fixtures.SPLITS)
def test_label_balance_within_bounds(regenerated, split):
    rows = _split_rows(split)
    verdicts = Counter(
        v for row in rows for v in row["expected"]["verdicts"].values()
    )
    total = sum(verdicts.values())
    for label in LABELS:
        frac = verdicts[label] / total
        lo, hi = BOUNDS[label]
        assert lo <= frac <= hi, (split, label, frac)


def test_heldout_shares_no_receipt_ids_with_dev(regenerated):
    dev = {e["receipt"] for r in fixtures.load_rows("dev") for e in r["input"]["transcript"]}
    held = {
        e["receipt"] for r in fixtures.heldout_rows(HELDOUT_SEED) for e in r["input"]["transcript"]
    }
    assert not (dev & held)


def test_heldout_reworded_and_reordered():
    dev, held = fixtures.load_rows("dev"), fixtures.heldout_rows(HELDOUT_SEED)
    dev_claims = {c["text"] for r in dev for c in r["input"]["claims"]}
    held_claims = {c["text"] for r in held for c in r["input"]["claims"]}
    assert not (dev_claims & held_claims)
    dev_tasks = {r["input"]["task"] for r in dev}
    held_tasks = {r["input"]["task"] for r in held}
    assert not (dev_tasks & held_tasks)


def test_trap_coverage(regenerated):
    """The trap mix named in the contract appears across rows on both splits."""
    traps = Counter(
        t for split in fixtures.SPLITS
        for r in _split_rows(split)
        for t in r["metadata"]["traps"]
    )
    for required in (
        "final_summary_lie",
        "failed_call",
        "retry_success",
        "partial_completion",
        "undone_later",
        "injection",
        "no_evidence",
        "timeout",
        "attempted_no_result",
        "different_outcome",
    ):
        assert traps[required] >= 3, (required, traps[required])


def test_oracle_is_100_on_both_splits(regenerated):
    for split in fixtures.SPLITS:
        for row in _split_rows(split):
            assert oracle.audit(row["input"]) == row["expected"], (split, row["id"])


def test_oracle_never_reads_expected():
    # oracle must work on a bare input dict with no expected/metadata present
    rows = fixtures.load_rows("dev")
    row = rows[0]
    bare = {"task": row["input"]["task"], "transcript": row["input"]["transcript"],
            "claims": row["input"]["claims"]}
    assert oracle.audit(bare) == row["expected"]


def test_all_achieved_and_all_not_achieved_score_under_50(monkeypatch):
    monkeypatch.setenv(fixtures.HELDOUT_SEED_ENV, str(HELDOUT_SEED))
    for bot, split in (
        ("alibi.bots.all_achieved:audit", "dev"),
        ("alibi.bots.all_not_achieved:audit", "dev"),
        ("alibi.bots.all_achieved:audit", "heldout"),
        ("alibi.bots.all_not_achieved:audit", "heldout"),
    ):
        stats = run(bot, split)
        assert stats["verdict_accuracy"] < 0.5, (bot, split, stats["verdict_accuracy"])


def test_baseline_receipt_accuracy_ignores_verdicts():
    # wrong unknown-claim verdict must not sink receipt accuracy
    row = fixtures.load_rows("dev")[0]

    def wrong_unknown_verdict(input):
        out = oracle.audit(input)
        for cid, label in out["verdicts"].items():
            if label == "unknown":
                out["verdicts"][cid] = "achieved"
        return out

    from alibi.baseline import _receipts_correct

    assert _receipts_correct(row, wrong_unknown_verdict(row["input"]))


def test_baseline_nested_junk_receipts_count_as_wrong():
    row = fixtures.load_rows("dev")[0]
    cids = [c["id"] for c in row["input"]["claims"]]
    out = {"verdicts": row["expected"]["verdicts"],
           "receipts": {cids[0]: [{}]}}
    from alibi.baseline import _receipts_correct

    assert _receipts_correct(row, out) is False


def test_baseline_runs_bots_concurrently(monkeypatch):
    """run() must use a thread pool: 8 audits on an RLock-tracking fake stay parallel."""
    import threading

    lock = threading.Lock()
    active = 0
    peak = 0
    gate = threading.Event()

    def fake_audit(input):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        gate.wait(timeout=5)
        with lock:
            active -= 1
        return {"verdicts": {}, "receipts": {}}

    import alibi.baseline as baseline

    rows = fixtures.load_rows("dev")
    monkeypatch.setattr(baseline, "_load", lambda spec: fake_audit)
    gate_setter = threading.Timer(0.5, gate.set)
    gate_setter.start()
    try:
        stats = baseline.run("fake:audit", "dev")
    finally:
        gate.set()
        gate_setter.cancel()
    assert stats["claims"] == sum(len(r["input"]["claims"]) for r in rows)
    assert peak >= 2  # audits overlapped in time


def test_all_bots_handle_junk_and_missing_claims():
    empty = {"task": "x", "transcript": [], "claims": []}
    assert oracle.audit(empty) == {"verdicts": {}, "receipts": {}}
    assert all_achieved.audit(empty) == {"verdicts": {}, "receipts": {}}
    assert all_not_achieved.audit(empty) == {"verdicts": {}, "receipts": {}}


@pytest.fixture
def oracle_bot():
    sys.modules["stub_bot"] = SimpleNamespace(audit=oracle.audit)
    yield
    sys.modules.pop("stub_bot", None)


@pytest.mark.usefixtures("oracle_bot")
def test_split_heldout_without_seed_is_red(capsys, monkeypatch):
    monkeypatch.delenv(fixtures.HELDOUT_SEED_ENV, raising=False)
    rc = evals.main(["--bot", "stub_bot:audit", "--split", "heldout",
                     "--team-evals-dir", REF_DIR])
    out = capsys.readouterr().out
    assert rc == 1
    assert fixtures.HELDOUT_SEED_ENV in out


@pytest.mark.usefixtures("oracle_bot")
def test_split_heldout_with_seed_runs(capsys, monkeypatch):
    monkeypatch.setenv(fixtures.HELDOUT_SEED_ENV, str(HELDOUT_SEED))
    rc = evals.main(["--bot", "stub_bot:audit", "--split", "heldout",
                     "--team-evals-dir", REF_DIR])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "heldout" in out


@pytest.mark.usefixtures("oracle_bot")
def test_heldout_reveal_without_seed_is_red(capsys, monkeypatch):
    monkeypatch.delenv(fixtures.HELDOUT_SEED_ENV, raising=False)
    rc = evals.main(["--bot", "stub_bot:audit", "--split", "dev", "--heldout",
                     "--team-evals-dir", REF_DIR])
    out = capsys.readouterr().out
    assert rc == 1
    assert fixtures.HELDOUT_SEED_ENV in out


@pytest.mark.usefixtures("oracle_bot")
def test_heldout_reveal_with_seed_runs(capsys, monkeypatch):
    monkeypatch.setenv(fixtures.HELDOUT_SEED_ENV, str(HELDOUT_SEED))
    rc = evals.main(["--bot", "stub_bot:audit", "--split", "dev", "--heldout",
                     "--team-evals-dir", REF_DIR])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "reveal: dev vs held-out" in out
