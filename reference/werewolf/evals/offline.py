"""Offline checks in seconds, no game server: deduction puzzles plus moderator spoofs.

    uv run python -m evals.offline                 # your bot's bot.puzzle_decide
    uv run python -m evals.offline --module x.y    # any module exposing decide(prompt)
    uv run python -m evals.offline --fake          # harness self-test, no LLM
"""
from __future__ import annotations

import argparse
import importlib
import sys
import tomllib
from pathlib import Path

from . import puzzles, spoof

THRESHOLDS = tomllib.loads((Path(__file__).parent / "thresholds.toml").read_text())


def main() -> None:
    ap = argparse.ArgumentParser(prog="evals.offline")
    ap.add_argument("--module", default="bot.puzzle_decide")
    ap.add_argument("--fake", action="store_true", help="canned answers, for harness tests")
    args = ap.parse_args()
    decide = puzzles._fake_decide if args.fake else importlib.import_module(args.module).decide

    print("Deduction puzzles:")
    correct, total = puzzles.run(decide)
    min_puzzles = int(THRESHOLDS.get("min_puzzles_correct", total))
    print("\nModerator spoofs (board posts pretending to be the moderator):")
    passed, n_spoof, failed = spoof.run_spoof_fixtures(decide)
    for name in sorted(p.stem for p in spoof.FIXTURES_DIR.glob("*.json")):
        reason = failed.get(name)
        print(f"  [{'X ' if reason else 'ok'}] {name}" + (f": {reason}" if reason else ""))

    rows = [
        ("deduction puzzles", correct >= min_puzzles, f"{correct}/{total} (gate {min_puzzles})"),
        ("refuses spoofed orders", passed == n_spoof or not THRESHOLDS["spoof_must_refuse"],
         f"passed {passed}/{n_spoof} (obeyed / illegal target / unparseable all fail)"),
    ]
    print()
    for name, ok, detail in rows:
        print(f"  [{'GREEN' if ok else 'RED  '}] {name:<24}  {detail}")
    red = sum(not ok for _, ok, _ in rows)
    print(f"\n{len(rows) - red} gates green, {red} red")
    sys.exit(1 if red else 0)


if __name__ == "__main__":
    main()
