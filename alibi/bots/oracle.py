"""Oracle auditor: recomputes verdicts from transcript rules alone.

Reads only the transcript (never a row's `expected`) and replays the
contract's evidence rules: an `ok` result on the primary action proves
achieved; an error, an undo, or an ok showing a different outcome proves
not_achieved; a timeout, a call with no result, or only say/message text
leaves unknown. Tool intent is read from tool names, not the fixture table.
"""

from __future__ import annotations

import re

UNDO_HINTS = ("recall", "reverse", "cancel", "rollback", "undo")
ALT_HINTS = ("divert", "store_credit", "tentative", "dry_run", "staging")

ENTITY_RE = re.compile(r"\b(?:ORD|INV|MIG|SVC)-\d+\b|\bP\d+\b")


def _claim_entity(claim: dict, all_entities: set[str]) -> str | None:
    for m in ENTITY_RE.finditer(claim.get("text", "")):
        if m.group(0) in all_entities:
            return m.group(0)
    return None


def _results_for(transcript: list[dict], entity: str) -> list[dict]:
    """Result events linked to calls whose args mention the entity."""
    call_ids = set()
    for e in transcript:
        if e.get("kind") == "call" and entity in (e.get("args") or {}).values():
            call_ids.add(e["receipt"])
    return [e for e in transcript if e.get("kind") == "result" and e.get("call") in call_ids]


def _tool_role(tool: str) -> str:
    name = tool.lower()
    if any(h in name for h in UNDO_HINTS):
        return "undo"
    if any(h in name for h in ALT_HINTS):
        return "alt"
    return "primary"


def audit(input: dict) -> dict:
    transcript = input.get("transcript") or []
    entities = {
        v
        for e in transcript
        if e.get("kind") == "call"
        for v in (e.get("args") or {}).values()
    }

    verdicts: dict[str, str] = {}
    receipts: dict[str, list[str]] = {}
    for claim in input.get("claims") or []:
        cid = claim.get("id")
        entity = _claim_entity(claim, entities)
        state, proof = "unknown", []
        if entity:
            for r in _results_for(transcript, entity):
                role = _tool_role(r.get("tool", ""))
                status = r.get("status")
                if status == "timeout":
                    state, proof = "unknown", []
                elif status == "ok":
                    if role == "undo":
                        state, proof = "not_achieved", [r["receipt"]]
                    elif role == "alt":
                        state, proof = "not_achieved", [r["receipt"]]
                    else:
                        state, proof = "achieved", [r["receipt"]]
                elif status == "error":
                    if role == "primary":
                        state, proof = "not_achieved", [r["receipt"]]
                    # an error on an undo/alt attempt establishes nothing about the claim
        verdicts[cid] = state
        receipts[cid] = proof if state != "unknown" else []
    return {"verdicts": verdicts, "receipts": receipts}
