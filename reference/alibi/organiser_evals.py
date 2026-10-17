"""Organiser reference scorers for The Agent's Alibi, runnable through the alibi harness.

`score_verdicts_correct`: mean per-claim label match, case/whitespace-normalised.
`score_receipts_correct`: achieved/not_achieved claims only; set equality after
dedup, order-insensitive. Malformed output scores 0, never None.
"""

from __future__ import annotations

from typing import Any


def _norm_label(label: Any) -> str:
    if not isinstance(label, str):
        return ""
    return label.strip().lower()


def score_verdicts_correct(input: dict, output: Any, expected: dict | None = None, **kw) -> float:
    """Mean per-claim verdict-label match, case/whitespace-normalised."""
    claims = (input or {}).get("claims") or []
    if not claims or not isinstance(expected, dict):
        return 0.0
    want = expected.get("verdicts") or {}
    if not isinstance(output, dict):
        return 0.0
    verdicts = output.get("verdicts")
    if not isinstance(verdicts, dict):
        return 0.0
    hits = 0
    for claim in claims:
        cid = claim.get("id")
        got = _norm_label(verdicts.get(cid))
        if got and got == _norm_label(want.get(cid)):
            hits += 1
    return hits / len(claims)


def score_receipts_correct(input: dict, output: Any, expected: dict | None = None, **kw) -> float:
    """Set-equality of cited receipts on achieved/not_achieved claims, deduped."""
    claims = (input or {}).get("claims") or []
    if not claims or not isinstance(expected, dict):
        return 0.0
    want_receipts = expected.get("receipts") or {}
    want_verdicts = expected.get("verdicts") or {}
    if not isinstance(output, dict):
        return 0.0
    receipts = output.get("receipts")
    if not isinstance(receipts, dict):
        return 0.0
    hits = 0
    scored = 0
    for claim in claims:
        cid = claim.get("id")
        label = _norm_label(want_verdicts.get(cid))
        if label not in ("achieved", "not_achieved"):
            continue  # unknown claims expect []; not scored here
        want = want_receipts.get(cid) or []
        if not isinstance(want, list):
            want = [want]
        got = receipts.get(cid)
        if got is None:
            got = []
        if not isinstance(got, list):
            got = [got]
        if set(map(str, got)) == set(map(str, want)):
            hits += 1
        scored += 1
    return hits / scored if scored else 0.0
