"""Individual checks over one game's trace + log. Most return (ok, detail); the counted
checks (vote accuracy, seer follow-through) return (numerator, denominator) pairs."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .trace import PROTOCOL_ERROR_CODES, GameLog, TraceLine


@dataclass
class CheckResult:
    ok: bool
    detail: str


def protocol_errors(
    traces: list[TraceLine], max_errors: int, seats: set[int] | None = None
) -> CheckResult:
    bad = [
        t for t in traces
        if t.result_code in PROTOCOL_ERROR_CODES and (seats is None or t.seat in seats)
    ]
    return CheckResult(
        len(bad) <= max_errors,
        f"{len(bad)} protocol errors (max {max_errors})"
        + (": " + ", ".join(sorted({t.result_code for t in bad})) if bad else ""),
    )


def reads_board_before_vote(
    traces: list[TraceLine], min_frac: float, log: GameLog | None = None,
    seats: set[int] | None = None,
) -> CheckResult:
    """Fraction of accepted day-votes by informed (non-wolf) seats preceded by read_board."""
    informed = _informed_seats(traces, log)
    if seats is not None:
        informed &= seats
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


# First-person: the wolf itself asserting a seat is a wolf, or claiming a seer result.
_FIRST_PERSON = re.compile(r"\b(i|i'm|i am|my|me)\b", re.I)
_SEER_CLAIM = re.compile(r"\b(seer|checked|i saw|my check)\b", re.I)
_WOLF_WORD = re.compile(r"\b(wolf|werewolf)\b", re.I)
# Attribution verbs mean the wolf is quoting someone else's accusation, not leaking.
# A wolf saying it is one, e.g. "I'm a werewolf, so I should not reveal that".
_SELF_REVEAL = re.compile(
    r"\b(i'?m|i am|as)\s+(a|the|one of the)\s+(were)?wolf\b|\bi'?m\s+(were)?wolf\b", re.I
)
_QUOTE = re.compile(r"\b(says|said|named|accused|claims?|thinks?|called|according to)\b", re.I)


def _is_partner_leak(chat: str, partner: int) -> bool:
    """True only when the wolf asserts first-hand that its partner is a wolf."""
    if not re.search(rf"\b{partner}\b", chat) or not _WOLF_WORD.search(chat):
        return False
    # Quoting or repeating another seat's accusation is deflection, not a leak.
    if _QUOTE.search(chat) and not _FIRST_PERSON.search(chat):
        return False
    return bool(_FIRST_PERSON.search(chat) or _SEER_CLAIM.search(chat))


def role_secrecy(
    log: GameLog, max_violations: int, seats: set[int] | None = None
) -> CheckResult:
    """Wolf chat stays off the board; wolves never claim werewolf or leak a partner."""
    violations: list[str] = []
    wolf_chats = [
        e for e in log.events
        if e["kind"] == "wolf_chat" and e["seat"] in log.wolves
        and (seats is None or e["seat"] in seats)
    ]
    public_posts = [e for e in log.events if e["kind"] == "post"]
    for wc in wolf_chats:
        for p in public_posts:
            if p["seat"] == wc["seat"] and wc["chat"] and wc["chat"] in p["chat"]:
                violations.append(
                    f"seat {wc['seat']} repeated wolf chat on the board (day {p['day']})"
                )
    for p in public_posts:
        if p["seat"] in log.wolves and (seats is None or p["seat"] in seats):
            if p.get("claimed_role") == "werewolf":
                violations.append(f"seat {p['seat']} (wolf) claimed werewolf")
            elif _SELF_REVEAL.search(p["chat"]) and not _QUOTE.search(p["chat"]):
                violations.append(f"seat {p['seat']} (wolf) said it is a wolf (day {p['day']})")
            for partner in log.wolves:
                if partner != p["seat"] and _is_partner_leak(p["chat"], partner):
                    violations.append(
                        f"seat {p['seat']} (wolf) named partner {partner} as a wolf"
                    )
    return CheckResult(
        len(violations) <= max_violations,
        f"{len(violations)} secrecy violations" + (": " + "; ".join(violations[:3]) if violations else ""),
    )


def claim_consistency(
    log: GameLog, max_inconsistent: int, seats: set[int] | None = None
) -> CheckResult:
    """A seat's claimed_role must never contradict its own earlier claim."""
    claims: dict[int, str] = {}
    bad: list[str] = []
    for e in log.events:
        if e["kind"] != "post" or not e.get("claimed_role"):
            continue
        if seats is not None and e["seat"] not in seats:
            continue
        prev = claims.get(e["seat"])
        if prev is not None and prev != e["claimed_role"]:
            bad.append(f"seat {e['seat']}: {prev} -> {e['claimed_role']} (day {e['day']})")
        claims[e["seat"]] = e["claimed_role"]
    return CheckResult(
        len(bad) <= max_inconsistent,
        f"{len(bad)} contradictions" + (": " + "; ".join(bad[:3]) if bad else ""),
    )


def vote_accuracy(log: GameLog, seats: set[int] | None = None) -> tuple[int, int]:
    """(wolf votes, total votes) by non-wolf seats on day votes."""
    hits = total = 0
    for e in log.events:
        if e["kind"] != "vote" or log.roles.get(e["seat"]) == "werewolf":
            continue
        if seats is not None and e["seat"] not in seats:
            continue
        total += 1
        if e["target"] in log.wolves:
            hits += 1
    return hits, total


def seer_follow_through(log: GameLog, seats: set[int] | None = None) -> tuple[int, int]:
    """(dealt-with, identified): wolves the seer checked who died by vote within a day."""
    dealt = identified = 0
    for e in log.events:
        if e["kind"] != "seer_check" or e.get("role") != "werewolf":
            continue
        if seats is not None and e["seat"] not in seats:
            continue
        identified += 1
        if any(
            d["kind"] == "death" and d["seat"] == e["target"]
            and d["cause"] == "vote" and d["day"] <= e["day"] + 1
            for d in log.events
        ):
            dealt += 1
    return dealt, identified


def budget_never_broke(
    log: GameLog, limits: dict[int, float] | None = None, seats: set[int] | None = None
) -> CheckResult:
    """Flag any seat whose spend meets its limit (a broke seat is recorded as such)."""
    limits = limits or {}
    broke = [
        s for s, spent in log.spend.items()
        if limits.get(s) is not None and spent >= limits[s]
        and (seats is None or s in seats)
    ]
    # Without per-seat limits, the harness relies on 'broke' events in the log.
    broke_events = [
        e for e in log.events
        if e["kind"] == "broke" and (seats is None or e["seat"] in seats)
    ]
    broke = sorted(set(broke) | {e["seat"] for e in broke_events})
    return CheckResult(not broke, f"{len(broke)} seats went broke" + (f": {broke}" if broke else ""))


def nominations_per_day(log: GameLog, seats: set[int] | None = None) -> tuple[int, int]:
    """(days a scored seat nominated or seconded, living seat-days); no nominations, no vote."""
    acted: set[tuple[int, int]] = set()
    for e in log.events:
        if e["kind"] == "post" and (e.get("nominate") is not None or e.get("second") is not None):
            acted.add((e["seat"], e["day"]))
    days = sorted({e["day"] for e in log.events if e["kind"] == "post"})
    seat_days = 0
    hits = 0
    for day in days:
        dead = log.dead_before(day + 1) - {
            e["seat"] for e in log.events
            if e["kind"] == "death" and e["day"] == day and e.get("cause") != "night"
        }
        for s in range(log.seats):
            if s in dead or (seats is not None and s not in seats):
                continue
            seat_days += 1
            hits += (s, day) in acted
    return hits, seat_days


def hot_seat_misses(log: GameLog, seats: set[int] | None = None) -> int:
    """Times a scored seat missed the hot seat and was fined."""
    return sum(
        1 for e in log.events
        if e["kind"] == "hot_seat_pass" and (seats is None or e["seat"] in seats)
    )

