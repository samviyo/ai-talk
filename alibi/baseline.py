"""Baseline runner: `python -m alibi.baseline --bot alibi.bots.naive:audit --split dev`.

Prints per-claim verdict accuracy, receipt-set accuracy (achieved/not_achieved
claims only, verdicts ignored), and accuracy per label and per trap. Heldout
rows come from the `ALIBI_HELDOUT_SEED` env seed, never from disk. Bot audits
run concurrently, 8 workers. A bot's junk output counts as wrong rather than
crashing the run.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

from .fixtures import heldout_rows, heldout_seed, load_rows

WORKERS = 8
LABELS = ("achieved", "not_achieved", "unknown")


def _load(fn_spec: str):
    module_name, _, fn_name = fn_spec.partition(":")
    fn = getattr(importlib.import_module(module_name), fn_name or "audit")
    return fn


def _normalise_label(value):
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if v in LABELS else None


def _verdict_correct(row: dict, output) -> bool:
    if not isinstance(output, dict):
        return False
    got = output.get("verdicts")
    if not isinstance(got, dict):
        return False
    for claim in row["input"]["claims"]:
        cid = claim["id"]
        want = row["expected"]["verdicts"][cid]
        if _normalise_label(got.get(cid)) != want:
            return False
    return True


def _receipts_correct(row: dict, output) -> bool:
    """Receipt-set accuracy only: unknown claims are skipped, verdicts ignored."""
    if not isinstance(output, dict):
        return False
    got_r = output.get("receipts")
    if not isinstance(got_r, dict):
        return False
    for claim in row["input"]["claims"]:
        cid = claim["id"]
        if row["expected"]["verdicts"][cid] == "unknown":
            continue
        want = row["expected"]["receipts"].get(cid, [])
        got = got_r.get(cid)
        if not isinstance(got, list) or not all(isinstance(r, str) for r in got):
            return False
        if set(got) != set(want):
            return False
    return True


def _claim_cells(row: dict, output):
    """Yield (claim_id, expected_label, correct?) per claim for breakdowns."""
    got_v = output.get("verdicts") if isinstance(output, dict) else None
    if not isinstance(got_v, dict):
        got_v = {}
    for claim in row["input"]["claims"]:
        cid = claim["id"]
        ok = _normalise_label(got_v.get(cid)) == row["expected"]["verdicts"][cid]
        yield cid, row["expected"]["verdicts"][cid], ok


def run(bot_fn_spec: str, split: str) -> dict:
    rows = heldout_rows(heldout_seed()) if split == "heldout" else load_rows(split)
    audit = _load(bot_fn_spec)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outputs = list(pool.map(audit, [row["input"] for row in rows]))

    total_claims = 0
    correct_claims = 0
    rows_verdict = 0
    rows_receipt = 0
    by_label: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [correct, total]
    by_trap: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    sample_wrong = None

    for row, output in zip(rows, outputs):
        row_ok = True
        for cid, label, ok in _claim_cells(row, output):
            total_claims += 1
            correct_claims += ok
            by_label[label][1] += 1
            by_label[label][0] += ok
            row_ok &= ok
        rows_verdict += row_ok
        rows_receipt += _receipts_correct(row, output)
        if not row_ok and sample_wrong is None:
            sample_wrong = (row, output)

    # per-trap: a row counts per claim verdict within that trap's rows
    for row, output in zip(rows, outputs):
        for cid, label, ok in _claim_cells(row, output):
            for trap in row["metadata"]["traps"]:
                by_trap[trap][1] += 1
                by_trap[trap][0] += ok

    return {
        "bot": bot_fn_spec,
        "split": split,
        "rows": len(rows),
        "claims": total_claims,
        "verdict_accuracy": correct_claims / total_claims,
        "row_verdict_accuracy": rows_verdict / len(rows),
        "receipt_accuracy": rows_receipt / len(rows),
        "by_label": {k: (c / t if t else 0.0, t) for k, (c, t) in by_label.items()},
        "by_trap": {k: (c / t if t else 0.0, t) for k, (c, t) in sorted(by_trap.items())},
        "sample_wrong": sample_wrong,
    }


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _print_report(stats: dict) -> None:
    print(f"bot={stats['bot']} split={stats['split']} rows={stats['rows']} claims={stats['claims']}")
    print(f"verdict accuracy (per claim): {_pct(stats['verdict_accuracy'])}")
    print(
        f"receipt-set accuracy (rows, achieved/not_achieved only): {_pct(stats['receipt_accuracy'])}"
    )
    print("by label:")
    for label, (acc, total) in sorted(stats["by_label"].items()):
        print(f"  {label:14s} {_pct(acc)}  (n={total})")
    print("by trap:")
    for trap, (acc, total) in stats["by_trap"].items():
        print(f"  {trap:22s} {_pct(acc)}  (n={total})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bot", default="alibi.bots.oracle:audit")
    parser.add_argument("--split", default="dev", choices=("dev", "heldout"))
    args = parser.parse_args()
    if args.split == "heldout":
        try:
            heldout_seed()
        except RuntimeError as e:
            print(e)
            sys.exit(1)
    stats = run(args.bot, args.split)
    _print_report(stats)
    if stats["sample_wrong"]:
        row, output = stats["sample_wrong"]
        print("sample wrong row:", row["id"], row["metadata"]["traps"])
        print("  output:", output)


if __name__ == "__main__":
    main()
