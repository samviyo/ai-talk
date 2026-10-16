"""Harness core: load team scorers, run them through Braintrust Eval, gate the means.

Vocabulary (README and HANDOFF use the same terms):
- task: one test case, a dataset row (one (game, scored seat) pair, or one puzzle/spoof).
- trial: one run of a task (Braintrust trial_count runs each task N times).
- transcript: the run's trace.jsonl + game.json + bot.log for one game.
- outcome: the final game state (GameLog: roles, events, spend, result).
- scorer: a plain function `(input, output, expected=None, **kw) -> float | bool | None`;
  None means "not applicable" and that trial is skipped in the mean.
- gate: a threshold on a scorer's mean in gates.toml (`[score_name] min = 0.8`, or `max`).
- suite: all your scorers plus their gates. Binary 0/1 scorers are preferred.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from braintrust import Eval
from braintrust.framework import ReporterDef

EVALS = Path(__file__).parent
NEGCTL = EVALS.parent / "tests" / "fixtures" / "runs" / "negctl"

# No-op reporter: `reporter=False` falls back to Braintrust's noisy default.
_QUIET = ReporterDef(
    name="quiet",
    report_eval=lambda evaluator, result, verbose, jsonl: result,
    report_run=lambda results, verbose, jsonl: True,
)


@dataclass
class Row:
    name: str
    ok: bool | None  # None = info (no gate)
    detail: str
    gate: bool = True
    exception: str = ""


def load_scorers(scorers_dir: Path) -> list[tuple[str, object]]:
    """Import every *.py in scorers_dir; return its score_* functions, name order."""
    scorers: list[tuple[str, object]] = []
    if not scorers_dir.is_dir():
        return scorers
    for path in sorted(scorers_dir.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        mod_name = f"scorers_{path.stem}"
        spec = importlib.util.spec_from_file_location(mod_name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        scorers.extend(
            (name, fn) for name, fn in sorted(vars(module).items())
            if name.startswith("score_") and callable(fn)
        )
    return scorers


def load_gates(gates_file: Path) -> dict[str, dict]:
    if not gates_file.exists():
        return {}
    return tomllib.loads(gates_file.read_text())


def gate_ok(mean: float, g: dict) -> bool:
    if "min" in g and mean < float(g["min"]):
        return False
    if "max" in g and mean > float(g["max"]):
        return False
    return True


def _wrap(fn, errors: dict[str, int]) -> object:
    """A raising scorer scores 0 (error_score) and the exception is reported."""
    def wrapped(input, output, expected=None, **kwargs):
        try:
            # Braintrust may forward extra kwargs (e.g. metadata); the contract
            # is (input, output, expected) — swallow the rest silently.
            val = fn(input, output, expected=expected)
        except Exception as exc:  # noqa: BLE001 — any scorer error becomes a 0
            errors[repr(exc)] = errors.get(repr(exc), 0) + 1
            return 0.0
        if val is None:
            return None
        return 1.0 if val is True else float(val)
    wrapped.__name__ = getattr(fn, "__name__", "score")
    return wrapped


def run_eval(name: str, data, task, scores, project: str | None = None,
             trial_count: int = 1):
    """One Braintrust Eval run; offline unless project is given (BT_SEND=1 sends)."""
    if project:
        return Eval(
            name=project, experiment_name=name, data=data, task=task, scores=scores,
            trial_count=trial_count, is_public=False,
        )
    return Eval(
        name=name, data=data, task=task, scores=scores, trial_count=trial_count,
        no_send_logs=True, reporter=_QUIET,
    )


def _mean(results, scorer_name: str) -> tuple[float | None, int, dict[str, int]]:
    """(mean over scored trials, n scored, exception counts) for one scorer."""
    total = 0.0
    n = 0
    for r in results:
        val = (r.scores or {}).get(scorer_name)
        if val is None:
            continue
        total += float(val)
        n += 1
    return (total / n if n else None), n, {}


def run_suite(views, scorers: list[tuple[str, object]], gates: dict[str, dict],
              title: str, project: str | None = None) -> list[Row]:
    """Run every scorer over every task once; return scoreboard rows."""
    # Braintrust reads row.metadata; dict rows, GameView as the task input.
    data = [{"input": v, "metadata": {"game": v.game, "seat": v.seat}} for v in views]
    rows: list[Row] = []
    for name, fn in scorers:
        errors: dict[str, int] = {}
        res = run_eval(
            name=f"{title}-{name}", data=data,
            task=lambda input: input,  # games are already played; the task is identity
            scores=[_wrap(fn, errors)], project=project,
        )
        mean, n, _ = _mean(res.results, name)
        if mean is None:
            rows.append(Row(name, None, "no applicable trials (returned None)", False,
                            exception="; ".join(errors) if errors else ""))
            continue
        detail = f"mean {mean:.2f} over {n} scored"
        gate = gates.get(name)
        if gate:
            bound = ", ".join(f"{k}={gate[k]}" for k in ("min", "max") if k in gate)
            ok = gate_ok(mean, gate) and not errors
            rows.append(Row(name, ok, f"{detail} (gate {bound})", True,
                           exception="; ".join(errors) if errors else ""))
        else:
            rows.append(Row(name, None, f"{detail} (no gate — shown as info)", False,
                            exception="; ".join(errors) if errors else ""))
    return rows


def negative_control(scorers: list[tuple[str, object]], gates: dict[str, dict],
                     project: str | None = None) -> list[Row]:
    """Run the suite over the known-bad negctl bot; a suite that passes it is dead."""
    if not any(gates.get(name) for name, _ in scorers):
        return []
    from .trace import game_views

    data = game_views(NEGCTL)
    rows = run_suite(data, scorers, gates, "negctl", project=project)
    live = any(r.gate and r.ok is False for r in rows)
    for r in rows:
        r.name = f"negctl: {r.name}"
        if live:
            r.gate, r.ok = False, None  # red on the bad bot means live, not a failure
    rows.insert(0, Row(
        "negative control", live,
        "your gates catch the always-vote-seat-0 bot" if live
        else "your suite passes a known-bad bot (always-vote-seat-0)", True,
    ))
    return rows


def print_scoreboard(rows: list[Row], title: str) -> int:
    """Print GREEN/RED/info rows; return the exit code (1 if any gated row is red)."""
    print(f"\n{title}:\n")
    width = max((len(r.name) for r in rows), default=20)
    red = 0
    for r in rows:
        if r.gate:
            mark = "GREEN" if r.ok else "RED  "
            red += 0 if r.ok else 1
        else:
            mark = "info "
        line = f"  [{mark}] {r.name:<{width}}  {r.detail}"
        if r.exception:
            line += f"  — raised: {r.exception}"
        print(line)
    gated = sum(1 for r in rows if r.gate)
    print(f"\n{gated - red} gates green, {red} red")
    return 1 if red else 0
