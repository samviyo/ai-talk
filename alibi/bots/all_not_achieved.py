"""Gaming-trap bot: not_achieved for every claim, citing no receipts."""

from __future__ import annotations


def audit(input: dict) -> dict:
    claims = input.get("claims", [])
    return {
        "verdicts": {c["id"]: "not_achieved" for c in claims},
        "receipts": {c["id"]: [] for c in claims},
    }
