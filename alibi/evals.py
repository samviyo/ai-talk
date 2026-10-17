"""Alibi eval harness on Braintrust `Eval()`: team scorers + gates, scoreboard, nonzero exit on red.

A row's `input` is fed to the auditor bot; the bot's raw return passes through
to the scorers unchanged. Offline unless `BT_SEND=1`. The bare GREEN/RED word
duplicates the colored one so it survives color stripping.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Callable

from .fixtures import heldout_rows, heldout_seed
from .scorers import Gate, Scorer, load_gates, load_scorers, wrapped

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"

SPLITS = ("dev", "heldout")
NEGATIVE_CONTROL = "alibi.bots.all_achieved:audit"
DEFAULT_BOT = "alibi.bots.naive:audit"
MAX_CONCURRENCY = 8

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"


def load_bot(spec: str) -> Callable[..., Any]:
    """Resolve `module:attr` or `module.attr` to the audit callable."""
    mod_name, _, attr = spec.partition(":")
    if not attr:
        mod_name, _, attr = spec.rpartition(".")
    import importlib
    module = importlib.import_module(mod_name)
    return getattr(module, attr)


def dataset_rows(split: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Rows for a split; `limit` keeps the first N. Heldout rows come from the env seed."""
    if split == "heldout":
        rows = heldout_rows(heldout_seed())
    else:
        path = DATASETS_DIR / f"{split}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return rows[:limit] if limit else rows


def make_task(bot):
    return lambda input: bot(input)


def run_suite(bot, scorers: list[Scorer], rows: list[dict[str, Any]],
              split: str, *, trials: int, send: bool) -> tuple[dict[str, Any], Any]:
    """Run one split (all rows, all scorers) via Braintrust Eval()."""
    from braintrust import Eval
    from braintrust.framework import ReporterDef

    # No-op reporter: `reporter=False` falls back to Braintrust's noisy default.
    quiet = ReporterDef(name="quiet", report_eval=lambda evaluator, result, verbose, jsonl: result,
                        report_run=lambda results, verbose, jsonl: True)
    score_fns = [wrapped(s.fn) for s in scorers]
    # Datum metadata is the only per-row identity that comes back on results.
    data = [{"input": r["input"], "expected": r.get("expected"),
             "metadata": {"id": r.get("id") or f"row-{i}"}}
            for i, r in enumerate(rows)]
    ev = Eval(
        name=f"alibi-{split}",
        data=data,
        task=lambda input: bot(input),
        scores=score_fns,
        trial_count=trials,
        max_concurrency=MAX_CONCURRENCY,
        no_send_logs=not send,
        reporter=quiet,
    )
    return {fn.__name__: fn for fn in score_fns}, ev


def _score_value(result, scorer_name: str) -> float | None:
    raw = getattr(result, "scores", None) or {}
    if isinstance(raw, dict):
        value = raw.get(scorer_name)
        if value is not None:
            return float(value)
    return None


def _result_scorer_errors(result) -> dict[str, Any]:
    """Scorers Braintrust itself failed to run (in result.metadata.scorer_errors)."""
    meta = getattr(result, "metadata", None) or {}
    errs = meta.get("scorer_errors") if isinstance(meta, dict) else None
    return errs if isinstance(errs, dict) else {}


def _first_error_text(wrapped_fns: dict[str, Any], scorer_name: str,
                      sdk_error: Any = None) -> str | None:
    local = getattr(wrapped_fns.get(scorer_name), "first_error", None)
    if local:
        return local
    if sdk_error:
        text = str(sdk_error).strip().splitlines()
        if text:
            return text[-1].strip()
    return None


def _row_key(result, index: int) -> str:
    meta = getattr(result, "metadata", None) or {}
    rid = meta.get("id") if isinstance(meta, dict) else None
    return str(rid) if rid is not None else f"row-{index}"


def _aggregate(wrapped_fns: dict[str, Any], ev, scorers: list[Scorer],
               trials: int) -> dict[str, dict]:
    """Fold Eval() output into per-scorer means, error counts, and pass^k."""
    agg = {s.name: {"values": [], "errors": 0, "pass_k": None,
                    "first_error": None} for s in scorers}
    by_case: dict[str, dict[str, list[float]]] = {s.name: {} for s in scorers}
    for index, result in enumerate(ev.results):
        task_error = getattr(result, "error", None)
        sdk_errors = _result_scorer_errors(result)
        key = _row_key(result, index)
        for scorer in scorers:
            entry = agg[scorer.name]
            if task_error is not None:
                # A bot that dies on a row must count against every scorer.
                entry["errors"] += 1
                if entry["first_error"] is None:
                    entry["first_error"] = (f"bot error {type(task_error).__name__}: "
                                            f"{task_error}")
                continue
            value = _score_value(result, scorer.name)
            if scorer.name in sdk_errors:
                # The SDK failed this scorer and recorded no score.
                entry["errors"] += 1
                if entry["first_error"] is None:
                    entry["first_error"] = _first_error_text(
                        wrapped_fns, scorer.name, sdk_errors[scorer.name])
                continue
            if value is None:
                continue
            if math.isnan(value):
                entry["errors"] += 1
                if entry["first_error"] is None:
                    entry["first_error"] = _first_error_text(wrapped_fns, scorer.name)
            else:
                entry["values"].append(value)
                by_case[scorer.name].setdefault(key, []).append(value)
    if trials > 1:
        # pass^k: a row passed k-of-k only if every trial scored it a hit.
        for scorer in scorers:
            hits = total = 0
            for vals in by_case[scorer.name].values():
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


def _negative_control_rows(scorers: list[Scorer], gates: dict[str, Gate],
                           rows: list[dict[str, Any]], *, trials: int,
                           send: bool) -> list[tuple[str, str, str]]:
    """all_achieved bot rows: GREEN only if a gate fails cleanly, with no scorer errors."""
    ctrl_bot = load_bot(NEGATIVE_CONTROL)
    ctrl_w, ctrl_ev = run_suite(ctrl_bot, scorers, rows, "negative-control",
                                trials=trials, send=send)
    ctrl_agg = _aggregate(ctrl_w, ctrl_ev, scorers, trials)
    ctrl_rows = build_rows(ctrl_agg, gates)
    # A scorer that errored is broken, not a gate that caught all_achieved.
    out: list[tuple[str, str, str]] = []
    live = False
    for name, verdict, detail in ctrl_rows:
        if ctrl_agg[name]["errors"]:
            e = ctrl_agg[name]
            why = f": {e['first_error']}" if e["first_error"] else ""
            out.append((f"all_achieved: {name}", "RED",
                        f"negative control (scorer error) ({e['errors']} row(s)){why}"))
        elif verdict == "RED":
            live = True
    out.insert(0, ("negative control", "GREEN" if live else "RED",
                   "all_achieved bot fails your gates" if live
                   else "your suite passes the all_achieved bot"))
    # The control bot's own rows are diagnostics, whatever their verdict.
    out.extend((f"all_achieved: {n}", "info", d) for n, _, d in ctrl_rows)
    return out


def _split_summary(rows: list[tuple[str, str, str]]) -> dict[str, str]:
    return {name: verdict for name, verdict, _ in rows}


def _side_by_side(dev_rows: list[tuple[str, str, str]],
                  heldout_rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Rows for the held-out reveal: dev verdict vs heldout verdict per scorer."""
    held = _split_summary(heldout_rows)
    out = []
    for name, verdict, detail in dev_rows:
        hv = held.get(name, "-")
        flag = "" if hv in (verdict, "info", "-") else "  <-- held-out differs"
        out.append((name, "RED" if (verdict == "RED" or hv == "RED") else (
            "GREEN" if verdict == "GREEN" else "info"),
            f"dev {verdict:<5} heldout {hv:<5}{flag}"))
    for name, verdict, detail in heldout_rows:
        if name not in _split_summary(dev_rows):
            out.append((name, "RED" if verdict == "RED" else "info",
                       f"dev -     heldout {verdict:<5}"))
    return out


def run_split(bot, scorers: list[Scorer], gates: dict[str, Gate], split: str,
              rows: list[dict[str, Any]], *, trials: int,
              send: bool) -> list[tuple[str, str, str]]:
    w, ev = run_suite(bot, scorers, rows, split, trials=trials, send=send)
    return build_rows(_aggregate(w, ev, scorers, trials), gates)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="alibi.evals")
    ap.add_argument("--bot", default=DEFAULT_BOT, help="bot spec module:attr")
    ap.add_argument("--split", default="dev", choices=SPLITS,
                    help="dataset split to run (heldout joins with --heldout)")
    ap.add_argument("--trials", type=int, default=1, help="trials per task")
    ap.add_argument("--limit", type=int, default=None, help="use only the first N rows")
    ap.add_argument("--heldout", action="store_true",
                    help="also run the heldout split and print dev vs held-out")
    ap.add_argument("--team-evals-dir", default=None,
                    help="override the team_evals dir (default: alibi/team_evals)")
    args = ap.parse_args(argv)
    send = os.environ.get("BT_SEND") == "1"

    scorers = load_scorers(args.team_evals_dir)
    gates = load_gates(args.team_evals_dir)
    bot = load_bot(args.bot)

    if not scorers:
        print_scoreboard(f"eval: {args.bot}", [
            ("no scorers yet", "RED",
             "write score_* functions in alibi/team_evals/*.py"),
        ])
        print(f"\n{RED}RED{RESET}\n")
        return 1
    if not gates:
        print_scoreboard(f"eval: {args.bot}", [
            ("no gates yet", "RED",
             "add [score_x] min = 0.8 tables to alibi/team_evals/gates.toml"),
        ])
        print(f"\n{RED}RED{RESET}\n")
        return 1

    try:
        rows = dataset_rows(args.split, args.limit)
    except FileNotFoundError:
        print(f"{RED}RED{RESET} missing alibi/datasets/{args.split}.jsonl\n")
        return 1
    except RuntimeError as e:
        # heldout with no reveal seed: RED, not a traceback
        print(f"{RED}RED{RESET} {e}\n")
        return 1
    if not rows:
        print_scoreboard(f"eval: {args.bot}", [
            ("no rows", "RED", f"alibi/datasets/{args.split}.jsonl is empty"),
        ])
        print(f"\n{RED}RED{RESET}\n")
        return 1

    ok = print_scoreboard(f"eval: {args.bot} [{args.split}]",
                          run_split(bot, scorers, gates, args.split, rows,
                                    trials=args.trials, send=send))

    if args.heldout:
        try:
            heldout = dataset_rows("heldout", args.limit)
        except RuntimeError as e:
            print(f"{RED}RED{RESET} {e}\n")
            return 1
        dev_rows = run_split(bot, scorers, gates, args.split, rows,
                             trials=args.trials, send=send)
        held_rows = run_split(bot, scorers, gates, "heldout",
                              heldout,
                              trials=args.trials, send=send)
        ok = print_scoreboard("reveal: dev vs held-out",
                              _side_by_side(dev_rows, held_rows)) and ok

    ctrl_rows = _negative_control_rows(scorers, gates, rows,
                                        trials=args.trials, send=send)
    ctrl_green = print_scoreboard("negative control: all_achieved bot", ctrl_rows)
    # Info rows on the control are diagnostics; only GREEN/RED verdict rows decide.
    ctrl_live = ctrl_rows[0][1] == "GREEN"
    ctrl_errors = any(v == "RED" and n != "negative control" for n, v, _ in ctrl_rows)
    ok = ok and ctrl_green and ctrl_live and not ctrl_errors

    print(f"\n{GREEN if ok else RED}{'GREEN' if ok else 'RED'}{RESET}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
