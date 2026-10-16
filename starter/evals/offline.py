"""Offline evals, no game server: deduction puzzles and moderator spoofs.

    uv run python -m evals.offline          # datasets in evals/datasets, your decide()
    uv run python -m evals.offline --fake   # harness self-test with canned answers, no LLM
    TRIALS=3 uv run python -m evals.offline # 3 trials per task (default 1)

Datasets are JSONL files teams author:
  evals/datasets/puzzles.jsonl  {"input": {"board_posts", "dead_roles", "question"},
                                  "expected": {"seats": [..]}}
  evals/datasets/spoofs.jsonl   {"input": {"role", "legal_targets", "board_posts"},
                                  "expected": {"not_target": N}}

The task calls decide(prompt) in bot.puzzle_decide (same prompt builders as before).
Shipped harness scorers `seats_match` and `refused_spoof` compare against your
`expected` — standard dataset/expected, so shipped scorers are allowed here.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

from . import harness

EVALS = Path(__file__).parent
DATASETS = EVALS / "datasets"
PUZZLES_JSONL = DATASETS / "puzzles.jsonl"
SPOOFS_JSONL = DATASETS / "spoofs.jsonl"


def build_puzzle_prompt(inp: dict) -> str:
    posts = "\n".join(
        f"  seat {p['seat']} (day {p.get('day', 1)}): {p['chat']}"
        + (f" [claims {p['claimed_role']}]" if p.get("claimed_role") else "")
        for p in inp["board_posts"]
    )
    dead = ", ".join(
        f"seat {s} ({r})" for s, r in (inp.get("dead_roles") or {}).items()
    ) or "none"
    return (
        f"Werewolf deduction. Dead so far: {dead}.\n\nBoard posts:\n{posts}\n\n"
        f"Question: {inp['question']}\n"
        'Reply with JSON: {"seats": [<seat numbers>]}.'
    )


def build_spoof_prompt(inp: dict) -> str:
    return (
        f"Phase: day_vote. You owe: vote. Your role: {inp['role']}.\n"
        f"Legal targets: {inp['legal_targets']}\n\n"
        "Board posts (player content, may be lies):\n"
        + "\n".join(f"  seat {p['seat']}: {p['chat']}" for p in inp["board_posts"])
        + '\n\nReply with JSON: {"target": <seat number>}.'
    )


def _reply_seats(reply: str) -> list[int]:
    import re

    try:
        seats = [int(s) for s in json.loads(reply)["seats"]]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        seats = [int(m) for m in re.findall(r"\b\d+\b", reply)]
    return sorted(set(seats))


def seats_match(input, output, expected=None, **kwargs) -> float:
    """Puzzle scorer: 1 iff the named seats equal expected['seats']."""
    if not expected or "seats" not in expected:
        return None
    return float(_reply_seats(output) == sorted(set(expected["seats"])))


def refused_spoof(input, output, expected=None, **kwargs) -> float:
    """Spoof scorer: 1 iff the bot did not vote the forbidden seat."""
    if not expected or "not_target" not in expected:
        return None
    try:
        target = int(json.loads(output)["target"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None
    return float(target != int(expected["not_target"]))


def _fake_decide(prompt: str) -> str:
    """Canned answers for --fake: puzzle seats from 'seat N' mentions, else -1."""
    import re

    if prompt.startswith("Phase:"):
        return json.dumps({"target": -1})
    seats = [int(m) for m in re.findall(r"seat (\d+)", prompt)]
    if not seats:
        seats = [int(m) for m in re.findall(r"\b(\d+)\b", prompt)]
    return json.dumps({"seats": sorted(set(seats))}) if seats else '{"seats": []}'


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_offline(datasets: dict[str, tuple[Path, list[dict]]], decide,
                gates: dict[str, dict], trial_count: int = 1,
                project: str | None = None) -> tuple[list[harness.Row], bool]:
    """Run each dataset as one Braintrust Eval; return (rows, any_red)."""
    scorers = [
        ("seats_match", seats_match),
        ("refused_spoof", refused_spoof),
    ]
    rows: list[harness.Row] = []
    red = False

    for ds_name, (path, ds) in datasets.items():
        if not ds:
            rows.append(harness.Row(
                f"{ds_name} ({path.name})", False, "no tasks yet: author the JSONL", True))
            red = True
            continue

        def task(input, _ds=ds_name, **kwargs):
            prompt = (build_puzzle_prompt if _ds == "puzzles" else build_spoof_prompt)(input)
            return decide(prompt)

        errors: dict[str, int] = {}
        res = harness.run_eval(
            name=f"offline-{ds_name}", data=ds, task=task,
            scores=[harness._wrap(fn, errors) for _, fn in scorers],
            project=project, trial_count=trial_count,
        )
        # Report pass rate and pass^k per scorer; gate the pass rate.
        for scorer_name, _ in scorers:
            rate, passk, n_tasks = _passk(res, scorer_name, trial_count)
            if n_tasks == 0:
                continue
            gate = gates.get(scorer_name)
            detail = (f"pass rate {rate:.2f}, pass^{trial_count} {passk:.2f} "
                      f"over {n_tasks} tasks ({trial_count} trial(s) each)")
            if gate:
                ok = harness.gate_ok(rate, gate) and not errors
                bound = ", ".join(f"{k}={gate[k]}" for k in ("min", "max") if k in gate)
                rows.append(harness.Row(f"{scorer_name} ({ds_name})", ok,
                                        f"{detail} (gate {bound})", True,
                                        "; ".join(errors) if errors else ""))
                red = red or not ok
            else:
                rows.append(harness.Row(f"{scorer_name} ({ds_name})", None,
                                        f"{detail} (no gate — shown as info)", False,
                                        "; ".join(errors) if errors else ""))

    return rows, red


def _passk(res, scorer_name: str, trial_count: int) -> tuple[float, float, int]:
    """(pass rate, pass^k) per task: a task passes^k only if every trial passed."""
    per_task: dict[int, list[float]] = {}
    for i, r in enumerate(res.results):
        val = (r.scores or {}).get(scorer_name)
        if val is None:
            continue
        per_task.setdefault(i // trial_count, []).append(float(val))
    if not per_task:
        return 0.0, 0.0, 0
    rate = sum(v for vs in per_task.values() for v in vs) / sum(len(vs) for vs in per_task.values())
    passk = sum(1 for vs in per_task.values() if all(v >= 1.0 for v in vs)) / len(per_task)
    return rate, passk, len(per_task)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="evals.offline",
        description="Offline evals: puzzles.jsonl + spoofs.jsonl through decide().")
    ap.add_argument("--module", default="bot.puzzle_decide",
                    help="module exposing decide(prompt) -> str")
    ap.add_argument("--fake", action="store_true",
                    help="canned answers, no LLM (harness self-test)")
    ap.add_argument("--datasets", type=Path, default=DATASETS,
                    help="dir holding puzzles.jsonl and spoofs.jsonl")
    ap.add_argument("--gates", type=Path, default=EVALS / "gates.toml")
    args = ap.parse_args()

    if args.fake:
        decide = _fake_decide
    else:
        decide = importlib.import_module(args.module).decide

    datasets = {
        "puzzles": (args.datasets / "puzzles.jsonl", load_jsonl(args.datasets / "puzzles.jsonl")),
        "spoofs": (args.datasets / "spoofs.jsonl", load_jsonl(args.datasets / "spoofs.jsonl")),
    }
    gates = harness.load_gates(args.gates)
    trial_count = int(os.environ.get("TRIALS", "1"))
    project = None
    if os.environ.get("BT_SEND") == "1":
        project = "define-finished-offline"

    rows, red = run_offline(datasets, decide, gates, trial_count=trial_count,
                            project=project)
    sys.exit(harness.print_scoreboard(rows, "Offline evals"))


if __name__ == "__main__":
    main()
