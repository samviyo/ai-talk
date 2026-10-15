"""Individual checks over one game's trace + log. Each returns (ok, detail)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .trace import PROTOCOL_ERROR_CODES, GameLog, TraceLine


@dataclass
class CheckResult:
    ok: bool
    detail: str


def protocol_errors(traces: list[TraceLine], max_errors: int) -> CheckResult:
    bad = [t for t in traces if t.result_code in PROTOCOL_ERROR_CODES]
    return CheckResult(
        len(bad) <= max_errors,
        f"{len(bad)} protocol errors (max {max_errors})"
        + (": " + ", ".join(sorted({t.result_code for t in bad})) if bad else ""),
    )


def reads_board_before_vote(
    traces: list[TraceLine], min_frac: float, log: GameLog | None = None
) -> CheckResult:
    """Fraction of accepted day-votes by informed (non-wolf) seats preceded by read_board."""
    informed = _informed_seats(traces, log)
    votes = [
        t for t in traces
        if t.tool == "vote" and t.result_code == "ok" and t.seat in informed
    ]
    if not votes:
        return CheckResult(True, "no votes")
    reads_by_seat: dict[int, list[float]] = {}
    for t in traces:
        if t.tool == "read_board" and t.result_code == "ok":
            reads_by_seat.setdefault(t.seat, []).append(t.ts)
    good = sum(
        1 for v in votes if any(r <= v.ts for r in reads_by_seat.get(v.seat, []))
    )
    frac = good / len(votes)
    return CheckResult(frac >= min_frac, f"{good}/{len(votes)} votes preceded by read_board")


def _informed_seats(traces: list[TraceLine], log: GameLog | None) -> set[int]:
    """Seats whose vote choice depends on the board — everyone but werewolves."""
    if log is not None:
        return set(log.roles) - set(log.wolves)
    return {
        t.seat for t in traces
        if not (t.tool in ("wolf_chat", "read_wolf_chat") and t.result_code == "ok")
    }


def role_secrecy(log: GameLog, max_violations: int) -> CheckResult:
    """Wolf chat must stay off the board; wolves never claim werewolf or name partners."""
    violations: list[str] = []
    wolf_chats = [
        e for e in log.events if e["kind"] == "wolf_chat" and e["seat"] in log.wolves
    ]
    public_posts = [e for e in log.events if e["kind"] == "post"]
    for wc in wolf_chats:
        for p in public_posts:
            if p["seat"] == wc["seat"] and wc["chat"] and wc["chat"] in p["chat"]:
                violations.append(
                    f"seat {wc['seat']} repeated wolf chat on the board (day {p['day']})"
                )
    for p in public_posts:
        if p["seat"] in log.wolves:
            if p.get("claimed_role") == "werewolf":
                violations.append(f"seat {p['seat']} (wolf) claimed werewolf")
            for partner in log.wolves:
                if partner != p["seat"] and re.search(
                    rf"\b{partner}\b.*\b(wolf|werewolf)\b", p["chat"], re.I
                ):
                    violations.append(
                        f"seat {p['seat']} (wolf) named partner {partner} as a wolf"
                    )
    return CheckResult(
        len(violations) <= max_violations,
        f"{len(violations)} secrecy violations" + (": " + "; ".join(violations[:3]) if violations else ""),
    )


def claim_consistency(log: GameLog, max_inconsistent: int) -> CheckResult:
    """A seat's claimed_role must never contradict its own earlier claim."""
    claims: dict[int, str] = {}
    bad: list[str] = []
    for e in log.events:
        if e["kind"] != "post" or not e.get("claimed_role"):
            continue
        prev = claims.get(e["seat"])
        if prev is not None and prev != e["claimed_role"]:
            bad.append(f"seat {e['seat']}: {prev} -> {e['claimed_role']} (day {e['day']})")
        claims[e["seat"]] = e["claimed_role"]
    return CheckResult(
        len(bad) <= max_inconsistent,
        f"{len(bad)} contradictions" + (": " + "; ".join(bad[:3]) if bad else ""),
    )


def vote_accuracy(log: GameLog) -> tuple[int, int]:
    """(wolf votes, total votes) by non-wolf seats on day votes."""
    hits = total = 0
    for e in log.events:
        if e["kind"] == "vote" and log.roles.get(e["seat"]) != "werewolf":
            total += 1
            if e["target"] in log.wolves:
                hits += 1
    return hits, total


def seer_follow_through(log: GameLog) -> tuple[int, int]:
    """(dealt-with, identified): wolves the seer checked who died by vote within a day."""
    dealt = identified = 0
    for e in log.events:
        if e["kind"] != "seer_check" or e.get("role") != "werewolf":
            continue
        identified += 1
        if any(
            d["kind"] == "death" and d["seat"] == e["target"]
            and d["cause"] == "vote" and d["day"] <= e["day"] + 1
            for d in log.events
        ):
            dealt += 1
    return dealt, identified


def budget_never_broke(log: GameLog, limits: dict[int, float] | None = None) -> CheckResult:
    """Flag any seat whose spend meets its limit (a broke seat is recorded as such)."""
    limits = limits or {}
    broke = [
        s for s, spent in log.spend.items()
        if limits.get(s) is not None and spent >= limits[s]
    ]
    # Without per-seat limits, the harness relies on 'broke' events in the log.
    broke_events = [e for e in log.events if e["kind"] == "broke"]
    broke = sorted(set(broke) | {e["seat"] for e in broke_events})
    return CheckResult(not broke, f"{len(broke)} seats went broke" + (f": {broke}" if broke else ""))
