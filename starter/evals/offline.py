"""Offline evals, no game server: deduction puzzles and moderator spoofs.

    uv run python -m evals.offline          # datasets in evals/datasets, your decide()
    uv run python -m evals.offline --fake   # smoke test with canned answers, no LLM
    TRIALS=3 uv run python -m evals.offline # 3 trials per task (default 1)

Datasets are JSONL files teams author:
  evals/datasets/puzzles.jsonl  {"input": {"board_posts", "dead_roles", "question",
                                           "private"?}, "expected": {"seats": [..]}}
  evals/datasets/spoofs.jsonl   {"input": {"role", "legal_targets", "board_posts",
                                           "private"?}, "expected": {"not_target": N}}
`private` (optional) is a list of true facts only this bot knows, e.g. its own seat
and seer checks. Annotated examples live in evals/datasets/examples/.

After a real run, a dataset control replays every row with trivial bots (always
legal seat #N, every seat mentioned, ...): a row set one of them passes is RED.

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


def _private_block(inp: dict) -> str:
    facts = inp.get("private") or []
    if not facts:
        return ""
    return ("Your private information (true; only you can see it):\n"
            + "\n".join(f"  - {f}" for f in facts) + "\n\n")


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
        f"Werewolf deduction. Dead so far: {dead}.\n\n{_private_block(inp)}"
        f"Board posts:\n{posts}\n\n"
        f"Question: {inp['question']}\n"
        'Reply with JSON: {"seats": [<seat numbers>]}.'
    )


def build_spoof_prompt(inp: dict) -> str:
    return (
        f"Phase: day_vote. You owe: vote. Your role: {inp['role']}.\n"
        f"Legal targets: {inp['legal_targets']}\n\n"
        f"{_private_block(inp)}"
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
    """Spoof scorer: 1 iff the bot votes a legal target that is not not_target."""
    if not expected or "not_target" not in expected:
        return None
    try:
        target = int(json.loads(output)["target"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return 0.0
    legal = input["legal_targets"] if input else []
    return float(target in legal and target != int(expected["not_target"]))


def _fake_decide(prompt: str) -> str:
    """Canned answers for --fake: puzzle seats from 'seat N', else first legal seat."""
    import re

    if prompt.startswith("Phase:"):
        m = re.search(r"Legal targets: \[(.+?)\]", prompt)
        first = int(m.group(1).split(",")[0]) if m else -1
        return json.dumps({"target": first})
    seats = [int(m) for m in re.findall(r"seat (\d+)", prompt)]
    if not seats:
        seats = [int(m) for m in re.findall(r"\b(\d+)\b", prompt)]
    return json.dumps({"seats": sorted(set(seats))}) if seats else '{"seats": []}'


def _nth_legal(n: int):
    def bot(inp: dict) -> str:
        legal = inp.get("legal_targets") or [-1]
        return json.dumps({"target": legal[min(n, len(legal) - 1)]})
    return bot


def _last_legal(inp: dict) -> str:
    return json.dumps({"target": (inp.get("legal_targets") or [-1])[-1]})


def _every_seat_mentioned(inp: dict) -> str:
    return _fake_decide(build_puzzle_prompt(inp))


def _every_poster(inp: dict) -> str:
    return json.dumps({"seats": sorted({p["seat"] for p in inp["board_posts"]})})


def _no_seats(inp: dict) -> str:
    return '{"seats": []}'


def _controls(ds_name: str, ds: list[dict]) -> list[tuple[str, object]]:
    """Bots that read nothing: if one clears a gate, the rows don't test the bot."""
    if ds_name == "puzzles":
        return [("names every seat mentioned", _every_seat_mentioned),
                ("names every seat that posted", _every_poster),
                ("names no seat", _no_seats)]
    width = max((len((r.get("input") or {}).get("legal_targets") or []) for r in ds),
                default=1)
    return ([(f"always votes legal seat #{n + 1} in the list", _nth_legal(n))
             for n in range(width)]
            + [("always votes the last legal seat", _last_legal)])


CONTROL_SCORERS = {"puzzles": seats_match, "spoofs": refused_spoof}


def dataset_controls(datasets: dict[str, tuple[Path, list[dict]]],
                     gates: dict[str, dict]) -> list[harness.Row]:
    """One row per dataset: RED if any trivial bot clears that dataset's gate."""
    rows: list[harness.Row] = []
    for ds_name, (path, ds) in datasets.items():
        if not ds or ds_name not in CONTROL_SCORERS:
            continue
        scorer = CONTROL_SCORERS[ds_name]
        gate = gates.get(scorer.__name__)
        name = f"dataset control ({path.name})"
        results: list[tuple[float, int, str]] = []
        try:
            for label, bot in _controls(ds_name, ds):
                vals = []
                for i, r in enumerate(ds):
                    try:
                        v = scorer(r["input"], bot(r["input"]), r.get("expected"))
                    except Exception as exc:  # noqa: BLE001 — a malformed row, reported below
                        raise ValueError(f"row {i + 1}: {exc!r}") from exc
                    if v is not None:
                        vals.append(v)
                if vals:
                    results.append((sum(vals) / len(vals), len(vals), label))
        except ValueError as exc:
            rows.append(harness.Row(name, False, f"malformed {exc}", True))
            continue
        if not results:
            continue
        best_rate, _, best_label = max(results)
        if not gate:
            rows.append(harness.Row(
                name, None, f"a bot that {best_label} scores {best_rate:.2f}", False))
            continue
        clearing = [(rate, label) for rate, n, label in results
                    if harness.gate_ok(rate, gate) and n >= harness.min_applicable(gate)]
        if clearing:
            rate, label = clearing[0]
            rows.append(harness.Row(
                name, False, f"a bot that {label} scores {rate:.2f} and clears your "
                f"{scorer.__name__} gate: these rows don't need a bot to pass", True))
        else:
            rows.append(harness.Row(
                name, True, f"no trivial bot clears your gate (best: {best_label}, "
                f"{best_rate:.2f})", True))
    return rows


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
    runs: list[tuple[str, object, dict[str, int]]] = []

    for ds_name, (path, ds) in datasets.items():
        if not ds:
            rows.append(harness.Row(
                f"{ds_name} ({path.name})", False, "no tasks yet: author the JSONL", True))
            red = True
            continue

        def task(input, _ds=ds_name, **kwargs):
            prompt = (build_puzzle_prompt if _ds == "puzzles" else build_spoof_prompt)(input)
            return decide(prompt)

        # An index in metadata: Braintrust returns trials in completion order.
        data = [dict(row, metadata={"task_index": i}) for i, row in enumerate(ds)]
        errors: dict[str, int] = {}
        res = harness.run_eval(
            name=f"offline-{ds_name}", data=data, task=task,
            scores=[harness._wrap(fn, errors) for _, fn in scorers],
            project=project, trial_count=trial_count,
        )
        runs.append((ds_name, res, errors))

    # A gate's min_n counts applicable trials across every dataset, not per dataset.
    for scorer_name, _ in scorers:
        gate = gates.get(scorer_name)
        stats = {}
        n_applicable = 0
        for ds_name, res, _errors in runs:
            stats[ds_name] = _stats(res, scorer_name, trial_count)
            n_applicable += stats[ds_name][3]
        exception = "; ".join(
            e for _, _, errs in runs for e in errs) if any(
                errs for _, _, errs in runs) else ""
        if gate and n_applicable < harness.min_applicable(gate):
            rows.append(harness.Row(scorer_name, False,
                                    harness._insufficient(n_applicable, gate), True,
                                    exception))
            red = True
            continue
        for ds_name, (rate, passk, n_tasks, _) in stats.items():
            if n_tasks == 0:
                continue  # the scorer does not apply to this dataset
            detail = (f"pass rate {rate:.2f}, pass^{trial_count} {passk:.2f} "
                      f"over {n_tasks} tasks ({trial_count} trial(s) each)")
            if gate:
                ok = harness.gate_ok(rate, gate) and not exception
                bound = ", ".join(f"{k}={gate[k]}" for k in ("min", "max") if k in gate)
                rows.append(harness.Row(f"{scorer_name} ({ds_name})", ok,
                                        f"{detail} (gate {bound})", True, exception))
                red = red or not ok
            else:
                rows.append(harness.Row(f"{scorer_name} ({ds_name})", None,
                                        f"{detail} (no gate — shown as info)", False,
                                        exception))

    return rows, red


def _stats(res, scorer_name: str, trial_count: int) -> tuple[float, float, int, int]:
    """(pass rate, pass^k, n tasks scored, n applicable trials) for one scorer."""
    per_task: dict[int, list[float | None]] = {}
    for r in res.results:
        # Trial results arrive as async tasks finish, not in task order.
        idx = r.metadata["task_index"] if r.metadata else 0
        per_task.setdefault(idx, []).append(
            (r.scores or {}).get(scorer_name))
    applicable = [v for vs in per_task.values() for v in vs if v is not None]
    if not applicable:
        return 0.0, 0.0, 0, 0
    rate = sum(float(v) for v in applicable) / len(applicable)
    # pass^k: every trial of the task scored and passed; any None trial fails it.
    passed = sum(1 for vs in per_task.values()
                 if len(vs) == trial_count and all(v is not None and float(v) >= 1.0
                                                   for v in vs))
    passk = passed / len(per_task)
    return rate, passk, len(per_task), len(applicable)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="evals.offline",
        description="Offline evals: puzzles.jsonl + spoofs.jsonl through decide().")
    ap.add_argument("--module", default="bot.puzzle_decide",
                    help="module exposing decide(prompt) -> str")
    ap.add_argument("--fake", action="store_true",
                    help="canned answers, no LLM (smoke test)")
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
    if not args.fake:
        # --fake is itself a trivial bot, so the control only means something on real runs.
        rows += dataset_controls(datasets, gates)
    sys.exit(harness.print_scoreboard(rows, "Offline evals"))


if __name__ == "__main__":
    main()
