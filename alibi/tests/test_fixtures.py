"""Offline tests for the fixtures: determinism, invariants, labels, disjointness."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from alibi import fixtures
from alibi.baseline import run
from alibi.bots import all_achieved, all_not_achieved, oracle

LABELS = ("achieved", "not_achieved", "unknown")
BOUNDS = {"achieved": (0.35, 0.45), "not_achieved": (0.30, 0.40), "unknown": (0.20, 0.30)}


def _reload_rows(split, tmp_path):
    path = tmp_path / f"{split}.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l]


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory):
    out = tmp_path_factory.mktemp("alibi_datasets")
    fixtures.regenerate(out)
    return out


@pytest.mark.parametrize("split", fixtures.SPLITS)
def test_regeneration_is_deterministic(tmp_path, split):
    fixtures.regenerate(tmp_path)
    first = (tmp_path / f"{split}.jsonl").read_text()
    fixtures.regenerate(tmp_path)
    second = (tmp_path / f"{split}.jsonl").read_text()
    assert first == second
    # and byte-identical to what is committed in the repo
    committed = (fixtures.DATASETS_DIR / f"{split}.jsonl").read_text()
    assert first == committed


def test_scorer_checks_regenerate_identically(tmp_path):
    fixtures.regenerate(tmp_path)
    assert (tmp_path / "scorer_checks.jsonl").read_text() == (
        fixtures.DATASETS_DIR / "scorer_checks.jsonl"
    ).read_text()


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
        assert set(c) >= {"id", "input", "expected", "output", "label", "why"}
        # the canned row must be a real dev row
        assert any(
            row["id"] == c["id"] or row["input"] == c["input"] for row in fixtures.load_rows("dev")
        )


@pytest.mark.parametrize("split", fixtures.SPLITS)
def test_row_counts_and_shape(regenerated, split):
    rows = _reload_rows(split, regenerated)
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
    for row in _reload_rows(split, regenerated):
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
    rows = _reload_rows(split, regenerated)
    verdicts = Counter(
        v for row in rows for v in row["expected"]["verdicts"].values()
    )
    total = sum(verdicts.values())
    for label in LABELS:
        frac = verdicts[label] / total
        lo, hi = BOUNDS[label]
        assert lo <= frac <= hi, (split, label, frac)


def test_heldout_shares_no_receipt_ids_with_dev(regenerated):
    dev = {e["receipt"] for r in _reload_rows("dev", regenerated) for e in r["input"]["transcript"]}
    held = {
        e["receipt"] for r in _reload_rows("heldout", regenerated) for e in r["input"]["transcript"]
    }
    assert not (dev & held)


def test_heldout_reworded_and_reordered():
    dev, held = fixtures.load_rows("dev"), fixtures.load_rows("heldout")
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
        for r in _reload_rows(split, regenerated)
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
        for row in _reload_rows(split, regenerated):
            assert oracle.audit(row["input"]) == row["expected"], (split, row["id"])


def test_oracle_never_reads_expected():
    # oracle must work on a bare input dict with no expected/metadata present
    rows = fixtures.load_rows("dev")
    row = rows[0]
    bare = {"task": row["input"]["task"], "transcript": row["input"]["transcript"],
            "claims": row["input"]["claims"]}
    assert oracle.audit(bare) == row["expected"]


def test_all_achieved_and_all_not_achieved_score_under_50():
    for bot, split in (
        ("alibi.bots.all_achieved:audit", "dev"),
        ("alibi.bots.all_not_achieved:audit", "dev"),
        ("alibi.bots.all_achieved:audit", "heldout"),
        ("alibi.bots.all_not_achieved:audit", "heldout"),
    ):
        stats = run(bot, split)
        assert stats["verdict_accuracy"] < 0.5, (bot, split, stats["verdict_accuracy"])


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
