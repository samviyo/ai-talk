"""Scorer-check runner: labelled rows in `alibi/datasets/scorer_checks.jsonl` check the scorers.

Runner rule: every gated scorer must pass (mean >= its gate on that row; None
counts as pass) on every correct and equivalent row, and at least one gated
scorer must fail cleanly on every wrong row. A gated scorer constant across
all check rows (always-true or always-false) is flagged too.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

from .scorers import Gate, Scorer, load_gates, load_scorers, wrapped

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"

CHECKS_PATH = Path(__file__).resolve().parent / "datasets" / "scorer_checks.jsonl"


def load_check_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_checks(scorers: list[Scorer], gates: dict[str, Gate],
               rows: list[dict[str, Any]],
               *, print_rows: bool = True) -> list[str]:
    """Run the rule; returns human-readable failures (empty = all green)."""
    failures: list[str] = []
    gated = [s for s in scorers if s.name in gates]
    if not gated:
        if print_rows:
            print(f"  {RED}RED  {RESET}no gates yet: add [score_x] min gates to gates.toml")
        return ["no gates yet"]

    # wrapped() turns raises into NaN and keeps the first error text.
    fns = {s.name: wrapped(s.fn) for s in gated}

    # values[scorer][row_id] -> float | None (None = "not applicable", NaN = raised)
    values: dict[str, dict[str, float | None]] = {
        s.name: {row["id"]: fns[s.name](input=row["input"], output=row["output"],
                                        expected=row.get("expected"))
                 for row in rows}
        for s in gated}

    for row in rows:
        rid, label, why = row["id"], row.get("label"), row.get("why", "")
        row_ok = True
        mentions: list[str] = []
        for s in gated:
            v = values[s.name][rid]
            gate = gates[s.name].min
            if v is None:
                mentions.append(f"{s.name}=None (pass)")
            elif math.isnan(v):
                row_ok = False
                err = getattr(fns[s.name], "first_error", None) or "scorer raised"
                mentions.append(f"{s.name}=error: {err}")
                failures.append(f"row {rid} ({label}): scorer {s.name} raised: {err}")
            elif label == "wrong":
                caught = v < gate
                mentions.append(f"{s.name}={v:.2f} ({'caught' if caught else 'missed'})")
            else:
                passed = v >= gate
                mentions.append(f"{s.name}={v:.2f} ({'pass' if passed else 'FAIL'})")
                if not passed:
                    row_ok = False
                    failures.append(f"row {rid} ({label}): gated scorer {s.name} "
                                    f"must pass (got {v:.2f}, min {gate:.2f})")
        if label == "wrong":
            caught = [s.name for s in gated
                      if (vv := values[s.name][rid]) is not None
                      and not math.isnan(vv) and vv < gates[s.name].min]
            if not caught:
                row_ok = False
                failures.append(f"row {rid} (wrong): no gated scorer fails; "
                                "a wrong output slips through every gate")
        elif label not in ("correct", "equivalent"):
            row_ok = False
            failures.append(f"row {rid}: unknown label {label!r}")

        if print_rows:
            color = GREEN if row_ok else RED
            verdict = "GREEN" if row_ok else "RED  "
            print(f"  {color}{verdict}{RESET} {rid:<12} {str(label):<11} {why}")
            for m in mentions:
                print(f"       {m}")

    # A gated scorer constant across all rows cannot discriminate.
    for s in gated:
        vs = list(values[s.name].values())
        if vs and all(v is not None and not math.isnan(v) and v == vs[0] for v in vs):
            failures.append(f"scorer {s.name} is constant ({vs[0]:.2f}) across all "
                            f"{len(vs)} check rows; it cannot tell correct from wrong")

    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="alibi.checks")
    ap.add_argument("--checks", default=str(CHECKS_PATH),
                    help="path to the scorer_checks.jsonl file")
    ap.add_argument("--team-evals-dir", default=None,
                    help="override the team_evals dir (default: alibi/team_evals)")
    args = ap.parse_args(argv)

    scorers = load_scorers(args.team_evals_dir)
    gates = load_gates(args.team_evals_dir)

    if not scorers:
        print(f"  {RED}RED  {RESET}no scorers yet: write score_* in alibi/team_evals/*.py")
        print(f"\n{RED}RED{RESET}\n")
        return 1

    path = Path(args.checks)
    if not path.exists():
        print(f"  {RED}RED  {RESET}missing {path}")
        print(f"\n{RED}RED{RESET}\n")
        return 1
    rows = load_check_rows(path)
    if not rows:
        print(f"  {RED}RED  {RESET}{path} is empty")
        print(f"\n{RED}RED{RESET}\n")
        return 1

    print(f"\n== scorer checks: {path.name} ==")
    failures = run_checks(scorers, gates, rows)
    if failures:
        print()
        for f in failures:
            print(f"  {RED}RED  {RESET}{f}")
        print(f"\n{RED}RED{RESET}\n")
        return 1
    print(f"\n{GREEN}GREEN{RESET}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
