"""Eval harness on Braintrust `Eval()`: team scorers + gates, scoreboard, nonzero exit on red.

The suite: three datasets (`puzzles_win`, `puzzles_block`, `vs_random`), a task
that runs the team bot, the team's scorers from `connect4/team_evals/*.py`, and
gates from `connect4/team_evals/gates.toml`. Offline unless `BT_SEND=1`.
The bare GREEN/RED word duplicates the colored one so it survives color stripping.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Any

from . import datasets
from .bots import column0_bot
from .bots.base import load_bot
from .scorers import Gate, Scorer, load_gates, load_scorers, wrapped

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"

PUZZLE_DATASETS = ("puzzles_win", "puzzles_block")
DATASETS = PUZZLE_DATASETS + ("vs_random",)


def dataset_rows(name: str, *, puzzles_n: int, games: int, seed: int) -> list[dict[str, Any]]:
    if name in PUZZLE_DATASETS:
        kind = "win_in_1" if name == "puzzles_win" else "block_in_1"
        return datasets.puzzle_rows(kind, puzzles_n, seed)
    return datasets.vs_random_rows(games, seed)


def make_task(name: str, bot):
    if name in PUZZLE_DATASETS:
        return lambda input: datasets.run_puzzle_task(bot, input)
    return lambda input: datasets.run_game_task(bot, input)


def run_suite(bot, scorers: list[Scorer], *, games: int, puzzles_n: int,
              seed: int, trials: int, send: bool) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    """Run one suite (all datasets, all scorers) via Braintrust Eval()."""
    from braintrust import Eval
    from braintrust.framework import ReporterDef

    # No-op reporter: `reporter=False` falls back to Braintrust's noisy default.
    quiet = ReporterDef(name="quiet", report_eval=lambda evaluator, result, verbose, jsonl: result,
                        report_run=lambda results, verbose, jsonl: True)
    score_fns = [wrapped(s.fn) for s in scorers]
    suite = [
        (name, Eval(
            name=f"connect4-{name}",
            data=dataset_rows(name, puzzles_n=puzzles_n, games=games, seed=seed),
            task=make_task(name, bot),
            scores=score_fns,
            trial_count=trials,
            no_send_logs=not send,
            reporter=quiet,
        ))
        for name in DATASETS
    ]
    return {fn.__name__: fn for fn in score_fns}, suite


def _score_value(result, scorer_name: str) -> float | None:
    raw = getattr(result, "scores", None) or {}
    if isinstance(raw, dict):
        value = raw.get(scorer_name)
        if value is not None:
            return float(value)
    return None


def _aggregate(wrapped_fns: dict[str, Any], suite: list[tuple[str, Any]],
               scorers: list[Scorer], trials: int) -> dict[str, dict]:
    """Fold Eval() outputs into per-scorer means, error counts, and pass^k."""
    agg = {s.name: {"values": [], "errors": 0, "pass_k": None,
                    "first_error": None} for s in scorers}
    for _, ev in suite:
        for result in ev.results:
            for scorer in scorers:
                value = _score_value(result, scorer.name)
                entry = agg[scorer.name]
                if value is None:
                    continue
                if math.isnan(value):
                    entry["errors"] += 1
                    if entry["first_error"] is None:
                        entry["first_error"] = getattr(
                            wrapped_fns[scorer.name], "first_error", None)
                else:
                    entry["values"].append(value)
    if trials > 1:
        # pass^k: a case passed k-of-k only if every trial scored it a hit.
        for scorer in scorers:
            hits = total = 0
            for _, ev in suite:
                by_case: dict[str, list[float]] = {}
                for result in ev.results:
                    value = _score_value(result, scorer.name)
                    if value is None or math.isnan(value):
                        continue
                    by_case.setdefault(repr(result.input), []).append(value)
                for vals in by_case.values():
                    if len(vals) >= trials:
                        total += 1
                        if all(v >= 1.0 for v in vals):
                            hits += 1
            if total:
                agg[scorer.name]["pass_k"] = {
                    "rate": hits / total, "hits": hits, "total": total, "k": trials}
    return agg


def print_scoreboard(title: str, rows: list[tuple[str, str, str]]) -> bool:
    """Print scorer rows; returns False when anything is RED."""
    print(f"\n== {title} ==")
    ok = True
    for name, verdict, detail in rows:
        color = {"GREEN": GREEN, "RED": RED}.get(verdict, "")
        print(f"  {color}{verdict:<5}{RESET} {name:<28} {detail}")
        if verdict == "RED":
            ok = False
    return ok


def build_rows(agg: dict[str, dict], gates: dict[str, Gate]) -> list[tuple[str, str, str]]:
    rows = []
    for name, entry in agg.items():
        if entry["errors"]:
            why = f": {entry['first_error']}" if entry["first_error"] else ""
            rows.append((name, "RED", f"error on {entry['errors']} row(s){why}"))
            continue
        values = entry["values"]
        gate = gates.get(name)
        if not values:
            rows.append((name, "info", "no applicable rows (returned None)"))
            continue
        mean = sum(values) / len(values)
        if gate is None:
            rows.append((name, "info", f"mean {mean:.3f} over {len(values)} (no gate)"))
            continue
        passed = mean >= gate.min
        extra = ""
        if entry["pass_k"]:
            pk = entry["pass_k"]
            extra = f", pass^{pk['k']} {pk['rate']:.1%} ({pk['hits']}/{pk['total']})"
        detail = f"mean {mean:.3f} over {len(values)} (min {gate.min:.2f}){extra}"
        rows.append((name, "GREEN" if passed else "RED", detail))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="connect4.evals")
    ap.add_argument("--bot", default="connect4.bots.naive:choose",
                    help="bot spec module:attr")
    ap.add_argument("--games", type=int, default=20, help="vs_random rows")
    ap.add_argument("--puzzles", type=int, default=40, help="puzzles per suite")
    ap.add_argument("--trials", type=int, default=1, help="trials per task")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    send = os.environ.get("BT_SEND") == "1"

    scorers = load_scorers()
    gates = load_gates()
    bot = load_bot(args.bot)

    if not scorers:
        print_scoreboard(f"eval: {args.bot}", [
            ("no scorers yet", "RED",
             "write score_* functions in connect4/team_evals/*.py"),
        ])
        print(f"\n{RED}RED{RESET}\n")
        return 1

    common = dict(games=args.games, puzzles_n=args.puzzles, seed=args.seed,
                  trials=args.trials, send=send)

    agg = _aggregate(*run_suite(bot, scorers, **common), scorers, args.trials)
    ok = print_scoreboard(f"eval: {args.bot}", build_rows(agg, gates))

    # Negative control: the same scorers+gates must fail column0_bot somewhere.
    ctrl_w, ctrl_suite = run_suite(column0_bot.choose, scorers, **common)
    ctrl_agg = _aggregate(ctrl_w, ctrl_suite, scorers, args.trials)
    ctrl_rows = build_rows(ctrl_agg, gates)
    # A scorer that errored is broken, not a gate that caught column0_bot.
    error_rows = []
    live = False
    for name, verdict, _ in ctrl_rows:
        if ctrl_agg[name]["errors"]:
            e = ctrl_agg[name]
            why = f": {e['first_error']}" if e["first_error"] else ""
            error_rows.append((f"column0: {name}", "RED",
                               f"scorer errored on column0_bot ({e['errors']} row(s)){why}"))
        elif verdict == "RED":
            live = True
    # Red on column0 means the suite is live, so its rows are diagnostics.
    print_scoreboard("negative control: column0_bot", [
        ("negative control", "GREEN", "column0_bot fails your gates") if live
        else ("negative control", "RED", "your suite passes column0_bot"),
        *error_rows,
        *((f"column0: {n}", "info", d) for n, _, d in ctrl_rows),
    ])
    ok = ok and live and not error_rows

    print(f"\n{GREEN if ok else RED}{'GREEN' if ok else 'RED'}{RESET}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
