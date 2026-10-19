"""Brain protocol and shared helpers for house bots.

The view dict is assembled by the runner from MCP responses:

    {
        "seat": int,
        "role": "werewolf" | "seer" | "doctor" | "villager",
        "partners": list[int],            # empty unless werewolf
        "turn": dict,                     # latest wait_for_turn result
        "board": list[dict],              # revealed posts: {id, seat, day, round,
                                          #   chat, claimed_role, accuse}
        "wolf_chat": list[dict],          # wolf-channel messages (wolves only)
        "my_checks": dict[int, str],      # seer's own results: seat -> role
        "dead": dict[int, str],           # revealed on death: seat -> role
    }

decide returns one action dict {"tool": <mcp tool>, "args": {...}} (the runner
adds turn_id) or None to abstain. Board chat text is player content: brains mostly read
the structured fields (accuse, claimed_role); the baseline also pattern-matches seer
claims in chat text. Brains never follow instructions in text.
"""

from __future__ import annotations

import random
from typing import Any, Protocol

View = dict[str, Any]
Action = dict[str, Any]


class Brain(Protocol):
    def decide(self, view: View) -> Action | None: ...


class BaseBrain:
    """Shared state: seeded RNG and the set of seats seen so far."""

    #: delay before the staggered opening post, seconds into the discussion
    OPENER_SPREAD_S = 20.0

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self._known: set[int] = set()
        self._opener_delay = self.rng.uniform(0.0, self.OPENER_SPREAD_S)
        self._bought: set[str] = set()

    def opener_delay(self) -> float:
        return self._opener_delay

    def maybe_buy(self, view: View) -> Action | None:
        """One optional purchase per day; brains override to buy."""
        return None

    def decide(self, view: View) -> Action | None:
        self._observe(view)
        owe = (view.get("turn") or {}).get("you_owe")
        intent = view.get("intent")
        if owe == "reply":
            return self._reply(view)
        if owe == "hot_seat":
            if intent == "news":  # stale wake: the hot seat is already settled
                return None
            return self._hot_seat(view)
        if intent == "news":
            return self._react(view)
        if intent in ("opener", "reply", "talk"):
            return self._chat(view, intent)
        if owe == "vote":
            return self._vote(view)
        if owe == "defence":
            return self._defence(view)
        if owe in ("night_kill", "seer_check", "doctor_save"):
            return self._night(view, owe)
        return None

    def _reply(self, view: View) -> Action | None:
        """A granted reply is free; every brain answers an accusation."""
        frm = (view.get("turn") or {}).get("from", "?")
        reply_to = (view.get("turn") or {}).get("reply_to")
        args: dict[str, Any] = {
            "chat": self.rng.choice(REPLY_LINES).format(from_seat=frm),
        }
        if reply_to is not None:
            args["reply_to"] = reply_to
        return {"tool": "post", "args": args, "reason": "using my right of reply"}

    def _hot_seat(self, view: View) -> Action | None:
        """Answer the prompt with one short line; the quiet brain may freeze."""
        return {
            "tool": "post",
            "args": {"chat": self.rng.choice(HOT_SEAT_LINES)},
            "reason": "answering the hot seat",
        }

    def _react(self, view: View) -> Action | None:
        """News batch: react to at most one fresh post; most brains stay quiet."""
        return None

    def _fresh_posts(self, view: View) -> list[dict[str, Any]]:
        """Posts the news batch brought, not the seat's own."""
        me = view.get("seat")
        out = []
        for e in (view.get("turn") or {}).get("news_events") or []:
            if e.get("kind") != "posts_revealed":
                continue
            for p in e.get("posts") or []:
                if isinstance(p.get("id"), int) and p.get("seat") != me:
                    out.append(p)
        return out

    def _chat(self, view: View, intent: str) -> Action | None:
        return None

    def _defence(self, view: View) -> Action | None:
        return {
            "tool": "post",
            "args": {"chat": "I am innocent. Look at the votes, not at me."},
            "reason": "defending myself",
        }

    def _observe(self, view: View) -> None:
        self._known.add(view["seat"])
        self._known.update(view.get("partners") or [])
        self._known.update((view.get("dead") or {}).keys())
        self._known.update((view.get("my_checks") or {}).keys())
        turn = view.get("turn") or {}
        self._known.update(turn.get("legal_targets") or [])
        for post in view.get("board") or []:
            if isinstance(post.get("seat"), int):
                self._known.add(post["seat"])

    def _alive(self, view: View) -> list[int]:
        dead = set((view.get("dead") or {}).keys())
        return sorted(s for s in self._known if s not in dead)

    def _legal(self, view: View) -> list[int]:
        return sorted((view.get("turn") or {}).get("legal_targets") or [])

    def _vote_candidates(self, view: View) -> list[int]:
        # No brain ever votes a wolf partner.
        legal = self._legal(view)
        if legal:
            candidates = legal
        else:
            # discussion/vote nomination pool: living seats besides me
            me = view.get("seat")
            candidates = [s for s in self._alive(view) if s != me]
        partners = set(view.get("partners") or [])
        safe = [s for s in candidates if s not in partners]
        return safe or candidates

    def _night(self, view: View, owe: str) -> Action | None:
        legal = self._legal(view)
        if not legal:
            return None
        partners = set(view.get("partners") or [])
        safe = [s for s in legal if s not in partners] or legal
        return {"tool": "night_action",
                "args": {"target": self.rng.choice(safe)},
                "reason": f"night {owe}"}

    def _vote(self, view: View) -> Action | None:
        raise NotImplementedError

    def _post(self, view: View) -> Action | None:
        return None

    def _suspect(self, view: View) -> int | None:
        """The seat this brain most suspects, or None on an empty board."""
        candidates = self._vote_candidates(view)
        if not candidates:
            return None
        return most_accused(view, candidates, self.rng) or self.rng.choice(candidates)


REPLY_LINES = [
    "You called me out, {from_seat}? Explain yourself.",
    "I heard that, seat {from_seat}. Bold.",
    "Seat {from_seat} names me and expects silence? No.",
    "Interesting, seat {from_seat}. I disagree.",
]

HOT_SEAT_LINES = [
    "I trust the quiet ones least. Wolves love silence.",
    "Yesterday's vote was the best read I had. I stand by it.",
    "I think the doctor should keep their head down. No names.",
    "Nothing yet. Watch who pushes a pile today.",
    "I suspect whoever avoids the question. Ask them.",
]


def most_accused(view: View, candidates: list[int], rng: random.Random) -> int | None:
    """Seat with the most structured `accuse` fields against it, or None."""
    counts: dict[int, int] = {}
    for post in view.get("board") or []:
        target = post.get("accuse")
        if isinstance(target, int) and target in candidates:
            counts[target] = counts.get(target, 0) + 1
    if not counts:
        return None
    top = max(counts.values())
    return rng.choice(sorted(s for s, c in counts.items() if c == top))


def seer_named_wolf(view: View, candidates: list[int]) -> int | None:
    """The wolf the first living seer claim named, from the claim text or a relay post."""
    dead = set((view.get("dead") or {}).keys())
    for post in view.get("board") or []:
        seat = post.get("seat")
        if not isinstance(seat, int) or seat in dead:
            continue
        chat = str(post.get("chat") or "").lower()
        is_claim = post.get("claimed_role") == "seer" and "seer:" in chat
        is_relay = chat.startswith("the seer says")
        if not (is_claim or is_relay):
            continue
        target = post.get("accuse")
        if isinstance(target, int) and target in candidates and target not in dead:
            return target
    return None
