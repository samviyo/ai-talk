"""Pure Werewolf rules engine: no I/O, no wall clock, all randomness from one seeded RNG."""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

Role: TypeAlias = Literal["werewolf", "seer", "doctor", "villager"]
Phase: TypeAlias = Literal["night", "day_discussion", "day_vote", "over"]
Result: TypeAlias = Literal["village", "wolves", "draw"]
Obligation: TypeAlias = Literal["night_kill", "seer_check", "doctor_save", "post", "vote"]

ROLES: tuple[str, ...] = ("werewolf", "seer", "doctor", "villager")
POST_MAX = 280
WOLF_CHAT_MAX = 500


class IllegalAction(Exception):
    """Any rejected act/abstain/wolf_chat; `code` is the machine-readable reason."""

    def __init__(self, code: str, msg: str = "") -> None:
        self.code = code
        self.msg = msg or code
        super().__init__(self.msg)


@dataclass(frozen=True)
class Config:
    seats: int
    wolves: int
    discussion_rounds: int
    max_days: int
    seed: int

    def __post_init__(self) -> None:
        if self.wolves < 1:
            raise ValueError("wolves must be >= 1")
        if self.seats < 2 * self.wolves + 3:
            raise ValueError(f"seats must be >= 2*wolves+3, got {self.seats}")
        if self.discussion_rounds < 0:
            raise ValueError("discussion_rounds must be >= 0")
        if self.max_days < 1:
            raise ValueError("max_days must be >= 1")


@dataclass(frozen=True)
class NightKill:
    target: int


@dataclass(frozen=True)
class SeerCheck:
    target: int


@dataclass(frozen=True)
class DoctorSave:
    target: int


@dataclass(frozen=True)
class Post:
    chat: str
    claimed_role: str | None = None
    accuse: int | None = None


@dataclass(frozen=True)
class Vote:
    target: int


Action: TypeAlias = NightKill | SeerCheck | DoctorSave | Post | Vote

_OBLIGATION_ACTIONS: dict[Obligation, type] = {
    "night_kill": NightKill,
    "seer_check": SeerCheck,
    "doctor_save": DoctorSave,
    "post": Post,
    "vote": Vote,
}


@dataclass(frozen=True)
class Event:
    """One log entry; audience None is public, empty frozenset is omniscient-only."""

    kind: str
    day: int
    round: int
    data: dict[str, Any] = field(compare=True, hash=False)
    audience: frozenset[int] | None = field(default=None, compare=False, hash=False)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "day": self.day, "round": self.round, **self.data}


class Game:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._rng = random.Random(config.seed)
        roles: list[Role] = (
            ["werewolf"] * config.wolves
            + ["seer", "doctor"]
            + ["villager"] * (config.seats - config.wolves - 2)
        )
        self._rng.shuffle(roles)
        self._roles: list[Role] = roles
        self._wolves: frozenset[int] = frozenset(
            s for s in range(config.seats) if roles[s] == "werewolf"
        )
        self._alive: list[bool] = [True] * config.seats
        self._events: list[Event] = []
        self._pending: dict[int, Obligation] = {}
        self._acted: set[int] = set()
        self._ballots: dict[int, Action] = {}
        self._post_seats: list[int] = []
        self._post_actions: list[Post] = []
        self._post_seq = 0
        self._last_save: int | None = None
        self.phase: Phase = "night"
        self.day = 1
        self.round = 0
        self.result: Result | None = None
        self.turn_id = ""
        for seat in range(config.seats):
            partners = sorted(self._wolves - {seat}) if seat in self._wolves else []
            self._emit(
                "role_assignment",
                {"seat": seat, "role": roles[seat], "partners": partners},
                audience=frozenset({seat}),
            )
        self._open_window()

    # --- introspection ---------------------------------------------------

    def role(self, seat: int) -> Role:
        return self._roles[seat]

    def alive(self) -> list[int]:
        return [s for s in range(self.config.seats) if self._alive[s]]

    def pending(self) -> dict[int, Obligation]:
        return {s: ob for s, ob in self._pending.items() if s not in self._acted}

    def legal_targets(self, seat: int) -> list[int]:
        obligation = self.pending().get(seat)
        alive = self.alive()
        if obligation == "night_kill":
            return [s for s in alive if s != seat and s not in self._wolves]
        if obligation == "seer_check":
            return [s for s in alive if s != seat]
        if obligation == "doctor_save":
            return [s for s in alive if s != self._last_save]
        if obligation == "vote":
            return [s for s in alive if s != seat]
        return []

    def events(self, viewer: int | None) -> list[Event]:
        if viewer is None:
            return list(self._events)
        if not 0 <= viewer < self.config.seats:
            raise ValueError(f"unknown seat {viewer}")
        return [e for e in self._events if e.audience is None or viewer in e.audience]

    # --- mutations ---------------------------------------------------------

    def act(self, seat: int, action: Action) -> None:
        obligation = self._check_turn(seat)
        expected = _OBLIGATION_ACTIONS[obligation]
        if not isinstance(action, expected):
            raise IllegalAction("bad_schema", f"{obligation} needs {expected.__name__}")
        if isinstance(action, Post):
            if len(action.chat) > POST_MAX:
                raise IllegalAction("too_long", f"chat > {POST_MAX} chars")
            if action.claimed_role is not None and action.claimed_role not in ROLES:
                raise IllegalAction("bad_schema", f"unknown role {action.claimed_role!r}")
            if action.accuse is not None and action.accuse not in self.alive():
                raise IllegalAction("invalid_target", f"cannot accuse {action.accuse}")
        elif action.target not in self.legal_targets(seat):
            raise IllegalAction("invalid_target", f"bad target {action.target}")
        self._acted.add(seat)
        if isinstance(action, SeerCheck):
            self._emit(
                "seer_result",
                {"seer": seat, "seat": action.target, "role": self._roles[action.target]},
                audience=frozenset({seat}),
            )
        elif isinstance(action, Post):
            self._post_seats.append(seat)
            self._post_actions.append(action)
        else:
            self._ballots[seat] = action
        self._maybe_close()

    def abstain(self, seat: int) -> None:
        self._check_turn(seat)
        self._acted.add(seat)
        self._maybe_close()

    def wolf_chat(self, seat: int, msg: str) -> None:
        if self.result is not None:
            raise IllegalAction("not_your_turn", "game over")
        self._check_seat(seat)
        if not self._alive[seat]:
            raise IllegalAction("dead_seat", f"seat {seat} is dead")
        if seat not in self._wolves:
            raise IllegalAction("not_your_turn", "werewolves only")
        if len(msg) > WOLF_CHAT_MAX:
            raise IllegalAction("too_long", f"wolf chat > {WOLF_CHAT_MAX} chars")
        self._emit("wolf_chat", {"seat": seat, "msg": msg}, audience=self._wolves)

    # --- internals ---------------------------------------------------------

    def _check_seat(self, seat: int) -> None:
        if not isinstance(seat, int) or not 0 <= seat < self.config.seats:
            raise IllegalAction("bad_schema", f"unknown seat {seat}")

    def _check_turn(self, seat: int) -> Obligation:
        if self.result is not None:
            raise IllegalAction("not_your_turn", "game over")
        self._check_seat(seat)
        if not self._alive[seat]:
            raise IllegalAction("dead_seat", f"seat {seat} is dead")
        obligation = self._pending.get(seat)
        if obligation is None:
            raise IllegalAction("not_your_turn", f"seat {seat} owes nothing now")
        if seat in self._acted:
            raise IllegalAction("already_acted", f"seat {seat} already acted")
        return obligation

    def _maybe_close(self) -> None:
        if len(self._acted) == len(self._pending):
            self._close_window()

    def _close_window(self) -> None:
        if self.phase == "night":
            self._resolve_night()
        elif self.phase == "day_discussion":
            self._reveal_posts()
        elif self.phase == "day_vote":
            self._resolve_vote()
        if self.result is None:
            self._advance_window()

    def _resolve_night(self) -> None:
        kill_votes = [a.target for a in self._ballots.values() if isinstance(a, NightKill)]
        save = next(
            (a.target for a in self._ballots.values() if isinstance(a, DoctorSave)), None
        )
        kill_target = None
        if kill_votes:
            counts = Counter(kill_votes)
            top = max(counts.values())
            tied = sorted(t for t, c in counts.items() if c == top)
            kill_target = self._rng.choice(tied)
        killed = kill_target if kill_target is not None and kill_target != save else None
        self._last_save = save
        self._emit(
            "night_result",
            {"kill_target": kill_target, "save_target": save, "killed": killed},
            audience=frozenset(),
        )
        if killed is not None:
            self._kill(killed, "night")
        else:
            self._emit("no_death", {"cause": "night"})
        self._check_end()

    def _reveal_posts(self) -> None:
        posts = []
        for seat, post in zip(self._post_seats, self._post_actions):
            posts.append(
                {
                    "id": self._post_seq,
                    "seat": seat,
                    "day": self.day,
                    "round": self.round,
                    "chat": post.chat,
                    "claimed_role": post.claimed_role,
                    "accuse": post.accuse,
                }
            )
            self._post_seq += 1
        self._emit("posts_revealed", {"posts": posts})

    def _resolve_vote(self) -> None:
        ballots = [
            {
                "seat": s,
                "target": self._ballots[s].target if s in self._ballots else None,
            }
            for s in self._pending
        ]
        targets = [b["target"] for b in ballots if b["target"] is not None]
        eliminated = None
        if targets:
            counts = Counter(targets)
            top = max(counts.values())
            tied = sorted(t for t, c in counts.items() if c == top)
            # a tie eliminates nobody, unlike the night's seeded tie-break
            if len(tied) == 1:
                eliminated = tied[0]
        self._emit("vote_tally", {"ballots": ballots, "eliminated": eliminated})
        if eliminated is not None:
            self._kill(eliminated, "vote")
        else:
            self._emit("no_death", {"cause": "vote"})
        self._check_end()

    def _advance_window(self) -> None:
        if self.phase == "night":
            if self.config.discussion_rounds > 0:
                self.phase, self.round = "day_discussion", 1
            else:
                self.phase, self.round = "day_vote", 0
        elif self.phase == "day_discussion":
            if self.round < self.config.discussion_rounds:
                self.round += 1
            else:
                self.phase, self.round = "day_vote", 0
        elif self.phase == "day_vote":
            if self.day >= self.config.max_days:
                self._finish("draw")
                return
            self.day += 1
            self.phase, self.round = "night", 0
        self._open_window()

    def _open_window(self) -> None:
        self._acted = set()
        self._ballots = {}
        self._post_seats = []
        self._post_actions = []
        if self.phase == "night":
            pending: dict[int, Obligation] = {}
            for s in self.alive():
                role = self._roles[s]
                if role == "werewolf":
                    pending[s] = "night_kill"
                elif role == "seer":
                    pending[s] = "seer_check"
                elif role == "doctor":
                    pending[s] = "doctor_save"
            self._pending = pending
        elif self.phase == "day_discussion":
            self._pending = {s: "post" for s in self.alive()}
        elif self.phase == "day_vote":
            self._pending = {s: "vote" for s in self.alive()}
        self.turn_id = f"{self.phase}:{self.day}:{self.round}"
        self._emit(
            "phase",
            {
                "phase": self.phase,
                "day": self.day,
                "round": self.round,
                "turn_id": self.turn_id,
            },
        )

    def _kill(self, seat: int, cause: str) -> None:
        self._alive[seat] = False
        self._emit("death", {"seat": seat, "role": self._roles[seat], "cause": cause})

    def _check_end(self) -> None:
        alive = self.alive()
        wolves = sum(1 for s in alive if s in self._wolves)
        if wolves == 0:
            self._finish("village")
        elif wolves >= len(alive) - wolves:
            self._finish("wolves")

    def _finish(self, result: Result) -> None:
        self.result = result
        self.phase = "over"
        self._pending = {}
        self._acted = set()
        self._emit(
            "phase",
            {"phase": "over", "day": self.day, "round": self.round, "turn_id": self.turn_id},
        )
        self._emit("result", {"result": result})

    def _emit(
        self,
        kind: str,
        data: dict[str, Any],
        audience: frozenset[int] | None = None,
    ) -> None:
        self._events.append(
            Event(kind=kind, day=self.day, round=self.round, data=data, audience=audience)
        )
