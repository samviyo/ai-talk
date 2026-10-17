"""Negative control: achieved for every claim, citing every say receipt."""

from __future__ import annotations


def audit(input: dict) -> dict:
    say_receipts = [e["receipt"] for e in input.get("transcript", []) if e.get("kind") == "say"]
    claims = input.get("claims", [])
    return {
        "verdicts": {c["id"]: "achieved" for c in claims},
        "receipts": {c["id"]: list(say_receipts) for c in claims},
    }
