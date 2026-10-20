"""Seeded scorer-check cases for the parity check: same row shape as `scorer_checks.jsonl`.

Each case is one transcript row from `alibi.fixtures` plus one bot output and a
label. Outputs mix exact answers, harmless rewrites, plausible mistakes and junk,
because a port that only handles tidy output is not a port.
"""

from __future__ import annotations

import json
import random
from typing import Any, Callable

from .. import fixtures

PRACTICE_SEED = 20261021
CASES_PER_SET = 36

Mutation = Callable[[dict, random.Random], Any]


def _deep(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def _claim_ids(row: dict) -> list[str]:
    return [c["id"] for c in row["input"]["claims"]]


def _receipts_of(row: dict, kind: str) -> list[str]:
    return [e["receipt"] for e in row["input"]["transcript"] if e.get("kind") == kind]


def _exact(row, rng):
    return _deep(row["expected"])


def _relabel_case(row, rng):
    out = _deep(row["expected"])
    style = rng.choice([str.upper, str.capitalize, lambda s: f"  {s}\t", lambda s: f"\u00a0{s}\u3000"])
    out["verdicts"] = {c: style(v) for c, v in out["verdicts"].items()}
    return out


def _duplicate_receipt(row, rng):
    out = _deep(row["expected"])
    cited = [c for c, v in out["receipts"].items() if v]
    if cited:
        c = rng.choice(cited)
        out["receipts"][c] = out["receipts"][c] + [out["receipts"][c][0]]
    return out


def _reorder_receipts(row, rng):
    out = _deep(row["expected"])
    out["receipts"] = {c: list(reversed(v)) for c, v in out["receipts"].items()}
    out["verdicts"] = dict(reversed(list(out["verdicts"].items())))
    return out


def _drop_empty_receipts(row, rng):
    out = _deep(row["expected"])
    out["receipts"] = {c: v for c, v in out["receipts"].items() if v}
    return out


def _flip_verdict(row, rng):
    out = _deep(row["expected"])
    c = rng.choice(_claim_ids(row))
    choices = [v for v in ("achieved", "not_achieved", "unknown") if v != out["verdicts"].get(c)]
    out["verdicts"][c] = rng.choice(choices)
    return out


def _cite_call(row, rng):
    out = _deep(row["expected"])
    calls = _receipts_of(row, "call")
    # Receipts on an unknown claim are not graded, so only a decided claim makes this wrong.
    decided = [c for c, v in out["verdicts"].items() if v != "unknown"] or _claim_ids(row)
    c = rng.choice(decided)
    out["receipts"][c] = [rng.choice(calls)] if calls else ["R-0"]
    return out


def _cite_say(row, rng):
    out = _deep(row["expected"])
    says = _receipts_of(row, "say") or _receipts_of(row, "message")
    c = rng.choice(_claim_ids(row))
    out["verdicts"][c] = "achieved"
    out["receipts"][c] = says[-1:]
    return out


def _drop_claim(row, rng):
    out = _deep(row["expected"])
    c = rng.choice(_claim_ids(row))
    out["verdicts"].pop(c, None)
    out["receipts"].pop(c, None)
    return out


def _all_achieved(row, rng):
    return {"verdicts": {c: "achieved" for c in _claim_ids(row)},
            "receipts": {c: [] for c in _claim_ids(row)}}


def _string_output(row, rng):
    return rng.choice(["All done - the agent said so.", "", "achieved"])


def _scalar_output(row, rng):
    return rng.choice([None, 0, 1, 2.5, True, []])


def _verdicts_not_dict(row, rng):
    out = _deep(row["expected"])
    out["verdicts"] = rng.choice([list(out["verdicts"].values()), "achieved", None])
    return out


def _receipts_not_dict(row, rng):
    out = _deep(row["expected"])
    out["receipts"] = rng.choice([[], "R-101", None])
    return out


def _odd_values(row, rng):
    out = _deep(row["expected"])
    c = rng.choice(_claim_ids(row))
    out["verdicts"][c] = rng.choice([1, None, True, ["achieved"]])
    out["receipts"][c] = rng.choice(["R-101", 101, None, [None]])
    return out


def _missing_keys(row, rng):
    out = _deep(row["expected"])
    out.pop(rng.choice(["verdicts", "receipts"]))
    return out


# (mutation, label, weight): labels say what a sound auditor check should conclude.
MUTATIONS: list[tuple[Mutation, str, int]] = [
    (_exact, "correct", 6),
    (_relabel_case, "equivalent", 3),
    (_duplicate_receipt, "equivalent", 3),
    (_reorder_receipts, "equivalent", 2),
    (_drop_empty_receipts, "equivalent", 2),
    (_flip_verdict, "wrong", 4),
    (_cite_call, "wrong", 3),
    (_cite_say, "wrong", 2),
    (_drop_claim, "wrong", 2),
    (_all_achieved, "wrong", 2),
    (_string_output, "wrong", 1),
    (_scalar_output, "wrong", 1),
    (_verdicts_not_dict, "wrong", 1),
    (_receipts_not_dict, "wrong", 1),
    (_odd_values, "wrong", 1),
    (_missing_keys, "wrong", 1),
]


def generate(seed: int, *, split: str = "heldout", n: int = CASES_PER_SET) -> list[dict]:
    """`n` labelled check rows built from the `split` transcripts generated from `seed`."""
    rng = random.Random(f"alibi-migrate:{split}:{seed}")
    base = [fixtures._public_row(r) for r in fixtures._build_split(split, seed)]
    fns = [m for m, _, _ in MUTATIONS]
    weights = [w for _, _, w in MUTATIONS]
    labels = {m: label for m, label, _ in MUTATIONS}
    # The first rows cover every mutation once; the rest are weighted draws.
    picks = fns + rng.choices(fns, weights=weights, k=max(0, n - len(fns)))
    rng.shuffle(picks)
    cases = []
    for i, mutate in enumerate(picks[:n]):
        row = rng.choice(base)
        cases.append({
            "id": f"{split[0]}{seed % 1000:03d}-{i + 1:03d}",
            "input": row["input"],
            "expected": row["expected"],
            "output": mutate(row, rng),
            "label": labels[mutate],
        })
    return cases


def dump(rows: list[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
