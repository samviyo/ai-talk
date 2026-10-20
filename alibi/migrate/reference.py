"""The Python check-alibi tool, staged: what the Zig port must reproduce, as canonical JSON.

parse  : rows.jsonl + gates.toml -> {"gates": {name: {min, min_n}}, "rows": [row]}
score  : parse -> {"gated": [name], "values": {scorer: {row_id: number | null | "error"}}}
report : parse + score -> {"green": bool, "rows": [{id, label, ok}],
                           "failures": [{kind, row, scorer}]}
run    : rows.jsonl + gates.toml -> score and report merged into one object
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any

from .. import checks
from ..scorers import Gate, Scorer, load_gates

ROW_KEYS = ("id", "label", "input", "output", "expected")


def parse(rows_path: Path, gates_dir: Path) -> dict[str, Any]:
    rows = checks.load_check_rows(rows_path)
    gates = load_gates(str(gates_dir))
    return {
        "gates": {name: {"min": g.min, "min_n": g.min_n} for name, g in gates.items()},
        "rows": [{k: row.get(k) for k in ROW_KEYS} for row in rows],
    }


def _value(v: float | None) -> float | str | None:
    if v is None:
        return None
    return "error" if math.isnan(v) else v


def stages(rows_path: Path, gates_dir: Path, scorers: list[Scorer]) -> dict[str, Any]:
    """All four canonical outputs from one pass of the Python tool."""
    parsed = parse(rows_path, gates_dir)
    gates = {n: Gate(min=g["min"], min_n=g["min_n"]) for n, g in parsed["gates"].items()}
    # Scorers may mutate rows in place, so they get a copy and golden parse stays as read.
    result = checks.evaluate(scorers, gates, copy.deepcopy(parsed["rows"]))
    score = {
        "gated": result["gated"],
        "values": {s: {rid: _value(v) for rid, v in vals.items()}
                   for s, vals in result["values"].items()},
    }
    report = {
        "green": not result["failures"],
        "rows": [{"id": r["id"], "label": r["label"], "ok": r["ok"]} for r in result["rows"]],
        "failures": [{"kind": f["kind"], "row": f.get("row"), "scorer": f.get("scorer")}
                     for f in result["failures"]],
    }
    return {"parse": parsed, "score": score, "report": report, "run": {**score, **report}}
