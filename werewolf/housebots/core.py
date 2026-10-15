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
adds turn_id) or None to abstain. Board chat text is player content: brains read
only the structured fields (accuse, claimed_role), never instructions in text.
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

    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self._known: set[int] = set()

    def decide(self, view: View) -> Action | None:
        self._observe(view)
        owe = (view.get("turn") or {}).get("you_owe")
        if owe == "vote":
            return self._vote(view)
        if owe == "post":
            return self._post(view)
        if owe in ("night_kill", "seer_check", "doctor_save"):
            return self._night(view, owe)
        return None

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
        partners = set(view.get("partners") or [])
        safe = [s for s in legal if s not in partners]
        return safe or legal

    def _night(self, view: View, owe: str) -> Action | None:
        legal = self._legal(view)
        if not legal:
            return None
        partners = set(view.get("partners") or [])
        safe = [s for s in legal if s not in partners] or legal
        return {"tool": "night_action", "args": {"target": self.rng.choice(safe)}}

    def _vote(self, view: View) -> Action | None:
        raise NotImplementedError

    def _post(self, view: View) -> Action | None:
        return None


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
