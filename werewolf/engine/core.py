"""Pure Werewolf rules engine: no I/O, no wall clock, all randomness from one seeded RNG."""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

Role: TypeAlias = Literal["werewolf", "seer", "doctor", "villager"]
Phase: TypeAlias = Literal[
    "night", "day_discussion", "defence", "day_vote", "reveal", "over"
]
Result: TypeAlias = Literal["village", "wolves", "draw"]
Obligation: TypeAlias = Literal["night_kill", "seer_check", "doctor_save", "defence", "vote"]

ROLES: tuple[str, ...] = ("werewolf", "seer", "doctor", "villager")
POST_MAX = 280
REASON_MAX = 200
WOLF_CHAT_MAX = 500
WHISPER_MAX = 280
MIN_POST_GAP_S = 5.0
DEFENCE_S = 15.0
MAX_STANDING = 3
REACTION_KINDS = ("agree", "doubt", "suspect")
MAX_REACTIONS_PER_DAY = 8
REPLY_GRANT_KINDS = ("accuse", "nominate", "second")

POWERUP_PRICES: dict[str, float] = {
    "extra_post": 0.01,
    "whisper": 0.016,
    "double_vote": 0.03,
    "shield": 0.04,
    "frame": 0.03,
}
POWERUPS_DAY = ("extra_post", "whisper", "shield")
DAY_WINDOWS = ("day_discussion", "defence", "day_vote")

_MENTION = re.compile(r"(?:@|seat\s+)(\d+)", re.I)


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
    max_days: int
    seed: int
    discussion_s: float = 60.0
    max_posts: int = 3

    def __post_init__(self) -> None:
        if self.wolves < 1:
            raise ValueError("wolves must be >= 1")
        if self.seats < 2 * self.wolves + 3:
            raise ValueError(f"seats must be >= 2*wolves+3, got {self.seats}")
        if self.max_days < 1:
            raise ValueError("max_days must be >= 1")
        if self.discussion_s <= 0:
            raise ValueError("discussion_s must be > 0")
        if self.max_posts < 1:
            raise ValueError("max_posts must be >= 1")


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
    reply_to: int | None = None
    nominate: int | None = None
    second: int | None = None


@dataclass(frozen=True)
class Vote:
    target: int


Action: TypeAlias = NightKill | SeerCheck | DoctorSave | Post | Vote

_OBLIGATION_ACTIONS: dict[Obligation, type] = {
    "night_kill": NightKill,
    "seer_check": SeerCheck,
    "doctor_save": DoctorSave,
    "defence": Post,
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
        self._last_save: int | None = None
        # discussion state
        self._posts: list[dict[str, Any]] = []
        self._pending_release: list[dict[str, Any]] = []  # accepted, not yet public
        # Server mode queues accepted posts for the floor; bare games publish at once.
        self.floor_queued = False
        self._post_seq = 0
        self._release_pos: dict[int, int] = {}  # post id -> index in release order
        self._posts_today: Counter[int] = Counter()
        self._post_times: dict[int, float] = {}
        self._extra_posts: dict[int, int] = {}
        self._nominations: dict[int, dict[str, Any]] = {}  # target -> {by, seconds}
        self._nominated_today: set[int] = set()
        self._seconded_today: set[int] = set()
        self._wakened: dict[int, set[int]] = {}  # target -> from-seats that already woke it
        self._day_cursors: dict[int, int] = {}  # seat -> board cursor at day start
        self._standing: list[int] = []
        self._defence_queue: list[int] = []
        self._reactions: dict[int, dict[str, list[int]]] = {}  # post_id -> kind -> seats
        self._reactions_today: Counter[int] = Counter()
        self._reacted_today: dict[int, set[int]] = {}  # post_id -> seats
        self._reply_grants: dict[int, dict[int, dict[str, Any]]] = {}  # target -> from -> grant
        self._replied_to: dict[int, set[int]] = {}  # target -> accusers already answered
        # night / power-up state
        self._seer_checks: dict[int, int] = {}
        self._frames: dict[int, int] = {}  # framed target -> buyer
        self._shields: set[int] = set()
        self._bought_today: dict[int, set[str]] = {}
        self._double_voters: set[int] = set()
        self._whispers: list[dict[str, Any]] = []  # delivered when the window closes
        self._beat_plan: list[dict[str, Any]] = []  # staged night beats left to emit
        self._beat_kill_votes: list[int] = []
        self._beat_save: int | None = None
        self._beat_seer_checks: dict[int, int] = {}
        self._vote_ballots: list[dict[str, Any]] = []  # ballots behind the reveal
        self._beat_tally: Counter[int] = Counter()  # running tally across reveals
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
            return list(self._standing)
        return []

    def window_s(self) -> float:
        """Nominal length of the open window, in seconds."""
        if self.phase == "day_discussion":
            return float(self.config.discussion_s)
        if self.phase == "defence":
            return DEFENCE_S
        return 20.0

    def events(self, viewer: int | None) -> list[Event]:
        if viewer is None:
            return list(self._events)
        if not 0 <= viewer < self.config.seats:
            raise ValueError(f"unknown seat {viewer}")
        return [e for e in self._events if e.audience is None or viewer in e.audience]

    def board_state(self, seat: int, since: int = 0) -> dict[str, Any]:
        """The public board from one seat's view: posts, nominations, standing."""
        # `since` indexes release order, not post id: queued posts publish out of id order.
        posts = [self._public_post(p) for p in self._posts[since:]]
        return {
            "posts": posts,
            "next": len(self._posts),
            "nominations": self.nominations(),
            "standing": list(self._standing),
            "posts_left": self.posts_left(seat),
            "reply_owed": self.reply_owed(seat),
        }

    def _public_post(self, post: dict[str, Any]) -> dict[str, Any]:
        return {
            **post,
            "reactions": {
                k: len(v) for k, v in self._reactions.get(post["id"], {}).items()
            },
        }

    def reply_owed(self, seat: int) -> dict[str, Any] | None:
        """An unused right-of-reply grant for this seat, oldest first."""
        grants = self._reply_grants.get(seat) or {}
        for g in grants.values():
            return {"reply_to": g["post_id"], "from": g["from"], "chat": g["chat"]}
        return None

    def consume_reply_grant(self, seat: int) -> dict[str, Any] | None:
        """Pop the grant a reply post is spending; None when there is none."""
        grants = self._reply_grants.get(seat) or {}
        for frm in sorted(grants):
            return grants.pop(frm)
        return None

    def public_cursor(self) -> int:
        return len(self._events)

    def public_events_after(self, seat: int, cursor: int) -> list[Event]:
        events = self.events(seat)
        if cursor >= len(events):
            return []
        return [e for e in events[cursor:] if e.audience is None]

    def nominations(self) -> list[dict[str, Any]]:
        return [
            {"target": t, "by": n["by"], "seconds": sorted(n["seconds"])}
            for t, n in sorted(self._nominations.items())
            if n.get("public", not self.floor_queued)
        ]

    def posts_left(self, seat: int) -> int:
        if self.phase != "day_discussion" or not self._alive[seat]:
            return 0
        limit = self.config.max_posts + self._extra_posts.get(seat, 0)
        return max(0, limit - self._posts_today.get(seat, 0))

    def unread_mentions(self, seat: int, cursor: int) -> list[dict[str, Any]]:
        """Unbatched mentions for a seat beyond its board cursor."""
        out: list[dict[str, Any]] = []
        for e in self._events:
            if e.kind != "mention":
                continue
            if e.audience is None or seat not in e.audience or e.data.get("batched"):
                continue
            if self._release_pos.get(e.data["post_id"], 0) < cursor:
                continue
            # A reply grant replaces the mention wake for that (from, target) pair.
            if e.data["from"] in (self._reply_grants.get(seat) or {}):
                e.data["batched"] = True
                continue
            out.append(e.data)
            e.data["batched"] = True
        return out

    # --- mutations ---------------------------------------------------------

    def act(self, seat: int, action: Action, now: float = 0.0) -> None:
        if isinstance(action, Post) and self.phase == "day_discussion":
            self._act_discussion_post(seat, action, now)
            return
        obligation = self._check_turn(seat)
        expected = _OBLIGATION_ACTIONS[obligation]
        if not isinstance(action, expected):
            raise IllegalAction("bad_schema", f"{obligation} needs {expected.__name__}")
        if isinstance(action, Post):
            self._validate_post(seat, action, obligation, now)
        elif action.target not in self.legal_targets(seat):
            raise IllegalAction("invalid_target", f"bad target {action.target}")
        self._acted.add(seat)
        if isinstance(action, SeerCheck):
            self._seer_checks[seat] = action.target
            self._emit_night_action(seat, "check", action.target)
            self._maybe_close()
        elif isinstance(action, NightKill):
            self._ballots[seat] = action
            self._emit_night_action(seat, "kill", action.target)
            self._maybe_close()
        elif isinstance(action, DoctorSave):
            self._ballots[seat] = action
            self._emit_night_action(seat, "save", action.target)
            self._maybe_close()
        elif isinstance(action, Post):
            self._record_post(seat, action, now)
            if obligation == "defence":
                self._maybe_close()
        elif isinstance(action, Vote):
            self._ballots[seat] = action
            # the projector shows who voted, not how; targets land at the reveal
            self._emit(
                "ballot_cast",
                {"seat": seat},
                audience=frozenset(),
            )
            self._maybe_close()

    def _act_discussion_post(self, seat: int, action: Post, now: float) -> None:
        """Discussion posts owe no obligation; the window is timed, not turn-based."""
        if self.result is not None:
            raise IllegalAction("not_your_turn", "game over")
        self._check_seat(seat)
        if not self._alive[seat]:
            raise IllegalAction("dead_seat", f"seat {seat} is dead")
        self._validate_post(seat, action, "post", now)
        self._record_post(seat, action, now)

    def _validate_post(
        self, seat: int, action: Post, obligation: Obligation | str, now: float
    ) -> None:
        if len(action.chat) > POST_MAX:
            raise IllegalAction("too_long", f"chat > {POST_MAX} chars")
        if action.claimed_role is not None and action.claimed_role not in ROLES:
            raise IllegalAction("bad_schema", f"unknown role {action.claimed_role!r}")
        if action.accuse is not None and action.accuse not in self.alive():
            raise IllegalAction("invalid_target", f"cannot accuse {action.accuse}")
        if action.reply_to is not None:
            found = next(
                (p for p in self._posts if p["id"] == action.reply_to), None
            )
            if found is None:
                raise IllegalAction("invalid_target", f"no public post {action.reply_to}")
            if found["day"] != self.day:
                raise IllegalAction(
                    "invalid_target", f"post {action.reply_to} is from another day"
                )
        if obligation == "defence":
            if action.nominate is not None or action.second is not None:
                raise IllegalAction("not_available", "no nominations in a defence")
            return
        grants = self._reply_grants.get(seat) or {}
        reply_grant = None
        if action.reply_to is not None:
            reply_grant = next(
                (g for g in grants.values() if g["post_id"] == action.reply_to), None
            )
        # A granted reply is free of the daily limit, the 5s gap and nomination rules.
        if reply_grant is None and action.nominate is None and action.second is None:
            if self.posts_left(seat) <= 0:
                raise IllegalAction(
                    "no_posts_left", f"seat {seat} is out of posts today"
                )
        if action.nominate is not None:
            if action.nominate not in self.alive():
                raise IllegalAction("invalid_target", f"cannot nominate {action.nominate}")
            if seat in self._nominated_today:
                raise IllegalAction("already_acted", "already nominated today")
        if action.second is not None:
            if action.second not in self._nominations:
                raise IllegalAction("invalid_target", f"nobody nominated {action.second}")
            if seat in self._seconded_today:
                raise IllegalAction("already_acted", "already seconded today")
            if self._nominations[action.second]["by"] == seat:
                raise IllegalAction("invalid_target", "cannot second your own nomination")

    def _record_post(self, seat: int, action: Post, now: float) -> dict[str, Any]:
        """Record an accepted post: counters and registries now, publicity on release."""
        post = {
            "id": self._post_seq,
            "seat": seat,
            "day": self.day,
            "round": self.round,
            "chat": action.chat,
            "claimed_role": action.claimed_role,
            "accuse": action.accuse,
            "reply_to": action.reply_to,
            "nominate": action.nominate,
            "second": action.second,
        }
        self._post_seq += 1
        is_reply = self.phase == "day_discussion" and self._spend_reply_grant(seat, action)
        post["is_reply"] = is_reply
        counts = (
            self._pending.get(seat) != "defence"
            and not (self.phase == "day_discussion" and is_reply)
        )
        if counts:
            self._posts_today[seat] += 1
            # The 5s gap runs from release; sim.py's release shim re-stamps it.
            self._post_times[seat] = now
        if action.nominate is not None:
            self._nominated_today.add(seat)
            self._nominations.setdefault(action.nominate, {"by": seat, "seconds": set()})
        if action.second is not None:
            self._seconded_today.add(seat)
            self._nominations[action.second]["seconds"].add(seat)
        if self._pending.get(seat) != "defence" and self.floor_queued:
            # Server mode: discussion posts go public only when the floor releases them.
            self._pending_release.append(post)
        else:
            self._publish_post(seat, post)
        return post

    def _spend_reply_grant(self, seat: int, action: Post) -> bool:
        grants = self._reply_grants.get(seat) or {}
        for frm in sorted(grants):
            if grants[frm]["post_id"] == action.reply_to:
                del grants[frm]
                self._replied_to.setdefault(seat, set()).add(frm)
                return True
        return False

    def clear_reply_grants(self, seat: int) -> None:
        self._reply_grants.pop(seat, None)

    def take_pending_release(self, seat: int) -> dict[str, Any] | None:
        """The server floor claims the seat's recorded-but-unpublished post."""
        for i, p in enumerate(self._pending_release):
            if p["seat"] == seat:
                return self._pending_release.pop(i)
        return None

    def release_post(self, seat: int, post: dict[str, Any]) -> None:
        """The floor releases one accepted post: board, nomination beats, mentions."""
        self._publish_post(seat, post)

    def _publish_post(self, seat: int, post: dict[str, Any]) -> None:
        self._release_pos[post["id"]] = len(self._posts)
        self._posts.append(post)
        public = self._public_post(post)
        public.pop("is_reply", None)  # internal bookkeeping, not board shape
        self._emit("posts_revealed", {"posts": [public]})
        if post["nominate"] is not None:
            nom = self._nominations.get(post["nominate"])
            if nom is not None and nom["by"] == seat:
                nom["public"] = True
            self._emit("nomination", {"seat": seat, "target": post["nominate"]})
        if post["second"] is not None:
            self._emit("seconded", {"seat": seat, "target": post["second"]})
        self._grant_replies(seat, post)
        self._emit_mentions(seat, post)

    def _grant_replies(self, seat: int, post: dict[str, Any]) -> None:
        for field_name in REPLY_GRANT_KINDS:
            target = post.get(field_name)
            if target is None or target == seat:
                continue
            if not (0 <= target < self.config.seats) or not self._alive[target]:
                continue
            grants = self._reply_grants.setdefault(target, {})
            # One grant per distinct accuser per day, answered or not.
            if seat in grants or seat in self._replied_to.get(target, set()):
                continue
            grants[seat] = {"post_id": post["id"], "from": seat, "chat": post["chat"]}
            self._emit(
                "reply_owed",
                {"seat": target, "from": seat, "post_id": post["id"]},
                audience=frozenset({target}),
            )

    def react(self, seat: int, post_id: int, kind: str) -> None:
        """Immediate public reaction; never queued behind the floor."""
        if self.result is not None:
            raise IllegalAction("not_your_turn", "game over")
        self._check_seat(seat)
        if not self._alive[seat]:
            raise IllegalAction("dead_seat", f"seat {seat} is dead")
        if self.phase not in ("day_discussion", "defence"):
            raise IllegalAction("not_available", "reactions only during the day talk")
        if kind not in REACTION_KINDS:
            raise IllegalAction("bad_schema", f"unknown reaction {kind!r}")
        post = next((p for p in self._posts if p["id"] == post_id), None)
        if post is None or post["day"] != self.day:
            raise IllegalAction("invalid_target", f"no public post {post_id}")
        if post["seat"] == seat:
            raise IllegalAction("invalid_target", "cannot react to your own post")
        if seat in self._reacted_today.setdefault(post_id, set()):
            raise IllegalAction("already_acted", "already reacted to this post")
        if self._reactions_today.get(seat, 0) >= MAX_REACTIONS_PER_DAY:
            raise IllegalAction("rate_limited", f"max {MAX_REACTIONS_PER_DAY} reactions a day")
        self._reacted_today[post_id].add(seat)
        self._reactions_today[seat] += 1
        self._reactions.setdefault(post_id, {}).setdefault(kind, []).append(seat)
        self._emit(
            "reaction",
            {"seat": seat, "post_id": post_id, "target_seat": post["seat"], "reaction": kind},
        )

    def _emit_night_action(self, seat: int, action: str, target: int) -> None:
        """Audience-only progress ping; targets stay hidden until the staged beat."""
        self._emit(
            "night_acted",
            {"seat": seat},
            audience=frozenset(),
        )

    def record_reason(self, seat: int, reason: str) -> None:
        """Audience-only hole-cam line; rejected once a seat is dead."""
        self._check_seat(seat)
        if not self._alive[seat]:
            return
        self._emit(
            "thought",
            {"seat": seat, "text": reason[:REASON_MAX]},
            audience=frozenset(),
        )

    def _emit_mentions(self, seat: int, post: dict[str, Any]) -> None:
        targets: set[int] = set()
        if post["reply_to"] is not None:
            parent = next(
                (p for p in self._posts if p["id"] == post["reply_to"]), None
            )
            if parent is not None:
                targets.add(parent["seat"])
        for m in _MENTION.finditer(post["chat"]):
            targets.add(int(m.group(1)))
        for t in sorted(targets):
            if t == seat or not (0 <= t < self.config.seats) or not self._alive[t]:
                continue
            woken = self._wakened.setdefault(t, set())
            if seat in woken:
                continue
            woken.add(seat)
            self._emit(
                "mention",
                {"seat": t, "from": seat, "post_id": post["id"], "chat": post["chat"]},
                audience=frozenset({t}),
            )

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

    # --- power-ups ----------------------------------------------------------

    def apply_powerup(
        self,
        seat: int,
        item: str,
        target: int | None = None,
        msg: str | None = None,
    ) -> dict[str, Any]:
        """Validate and apply a purchase; the server charges the ledger."""
        if self.result is not None:
            raise IllegalAction("not_your_turn", "game over")
        self._check_seat(seat)
        if not self._alive[seat]:
            raise IllegalAction("dead_seat", f"seat {seat} is dead")
        if item not in POWERUP_PRICES:
            raise IllegalAction("not_available", f"unknown item {item!r}")
        bought = self._bought_today.setdefault(self.day, set())
        key_once_day = f"{item}:{seat}"
        if item in ("extra_post", "whisper", "shield", "frame") and key_once_day in bought:
            raise IllegalAction("already_bought", f"{item} already bought today/tonight")
        if item == "extra_post":
            if self.phase != "day_discussion":
                raise IllegalAction("not_available", "extra_post only during discussion")
            self._extra_posts[seat] = self._extra_posts.get(seat, 0) + 1
        elif item == "whisper":
            if self.phase not in DAY_WINDOWS:
                raise IllegalAction("not_available", "whisper only during day windows")
            if target is None or target == seat or target not in self.alive():
                raise IllegalAction("invalid_target", f"bad whisper target {target}")
            if msg is None or not msg:
                raise IllegalAction("bad_schema", "whisper needs a msg")
            if len(msg) > WHISPER_MAX:
                raise IllegalAction("too_long", f"whisper > {WHISPER_MAX} chars")
            self._whispers.append({"seat": seat, "target": target, "msg": msg})
        elif item == "double_vote":
            if self.phase != "day_vote":
                raise IllegalAction("not_available", "double_vote only in the vote window")
            if seat in self._double_voters:
                raise IllegalAction("already_bought", "double_vote already bought this game")
            self._double_voters.add(seat)
        elif item == "shield":
            if self.phase not in DAY_WINDOWS:
                raise IllegalAction("not_available", "shield only during day windows")
            if seat in self._wolves:
                raise IllegalAction("not_available", "wolves cannot buy a shield")
            if self.day >= self.config.max_days:
                raise IllegalAction("not_available", "no shield on the last day")
            self._shields.add(seat)
        elif item == "frame":
            if self.phase != "night":
                raise IllegalAction("not_available", "frame only during a night window")
            if seat not in self._wolves:
                raise IllegalAction("not_available", "wolves only")
            if target is None or target not in self.alive() or target in self._wolves:
                raise IllegalAction("invalid_target", f"bad frame target {target}")
            self._frames[target] = seat
            self._emit_night_action(seat, "frame", target)
        if item != "double_vote":
            bought.add(key_once_day)
        self._emit(
            "powerup",
            {"seat": seat, "item": item, "target": target},
            audience=frozenset({seat}),
        )
        return {"price_usd": POWERUP_PRICES[item]}

    def shop(self, seat: int) -> list[dict[str, Any]]:
        """Availability per item without charging; mirrors apply_powerup's rules."""
        out = []
        for item, price in POWERUP_PRICES.items():
            entry: dict[str, Any] = {"item": item, "price_usd": price, "available": False}
            try:
                self._check_powerup(seat, item)
                entry["available"] = True
            except IllegalAction as e:
                entry["reason"] = e.code
            out.append(entry)
        return out

    def _check_powerup(self, seat: int, item: str) -> None:
        if self.result is not None or not self._alive[seat]:
            raise IllegalAction("not_available")
        bought = self._bought_today.get(self.day, set())
        if f"{item}:{seat}" in bought:
            raise IllegalAction("already_bought")
        if item == "extra_post":
            if self.phase != "day_discussion":
                raise IllegalAction("not_available")
        elif item == "whisper":
            if self.phase not in DAY_WINDOWS:
                raise IllegalAction("not_available")
        elif item == "double_vote":
            if self.phase != "day_vote" or seat in self._double_voters:
                raise IllegalAction("not_available")
        elif item == "shield":
            if (
                self.phase not in DAY_WINDOWS
                or seat in self._wolves
                or self.day >= self.config.max_days
            ):
                raise IllegalAction("not_available")
        elif item == "frame":
            if self.phase != "night" or seat not in self._wolves:
                raise IllegalAction("not_available")
        else:
            raise IllegalAction("not_available", f"unknown item {item!r}")

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
        if self.phase == "day_discussion":
            return  # timed window: only close_discussion() ends it
        if len(self._acted) == len(self._pending):
            self._close_window()

    def _close_window(self) -> None:
        """Night, defence and vote windows close when every obligation clears."""
        self._flush_whispers()
        if self.phase == "night":
            self._resolve_night()
            # a non-empty beat plan opens the reveal phase; dawn follows it
            if self.result is None and self.phase == "night":
                self._start_day()
                self._open_window("day_discussion")
        elif self.phase == "defence":
            self._next_defence()
        elif self.phase == "day_vote":
            self._resolve_vote()
            # a non-empty ballot plan opens the reveal phase; the day ends after it
            if self.result is None and self.phase == "day_vote":
                self._finish_day()
        elif self.phase == "reveal":
            self.advance_reveal()

    def close_discussion(self) -> None:
        """Timed close of the discussion window: pick nominees, open defences."""
        if self.phase != "day_discussion":
            raise IllegalAction("not_your_turn", "no discussion window open")
        # Unused right-of-reply grants expire quietly when the floor closes.
        self._reply_grants = {}
        self._flush_whispers()
        ranked = sorted(
            self._nominations.items(),
            key=lambda kv: (-len(kv[1]["seconds"]), self._rng.random()),
        )
        self._standing = sorted(t for t, _ in ranked[:MAX_STANDING])
        if self._standing:
            self._emit("standing", {"targets": list(self._standing)})
            self._defence_queue = list(self._standing)
            self._next_defence()
        else:
            self._emit("vote_skipped", {"silent": self._silent_seats()})
            self._finish_day()

    def _finish_day(self) -> None:
        if self.day >= self.config.max_days:
            self._finish("draw")
        else:
            self.day += 1
            self._open_window("night")

    def _flush_whispers(self) -> None:
        """Whispers bought in a window land when it closes."""
        for w in self._whispers:
            self._emit(
                "whisper",
                {"seat": w["seat"], "target": w["target"], "msg": w["msg"]},
                audience=frozenset({w["seat"], w["target"]}),
            )
        self._whispers = []

    def _silent_seats(self) -> list[int]:
        return [
            s
            for s in self.alive()
            if self._posts_today.get(s, 0) == 0 and self._reactions_today.get(s, 0) == 0
        ]

    def _next_defence(self) -> None:
        """Open the next standing nominee's defence window, or the vote."""
        while self._defence_queue:
            seat = self._defence_queue.pop(0)
            if self._alive[seat]:
                self._open_window("defence", seat)
                return
        self._open_window("day_vote")

    def _resolve_night(self) -> None:
        kill_votes = [a.target for a in self._ballots.values() if isinstance(a, NightKill)]
        save = next(
            (a.target for a in self._ballots.values() if isinstance(a, DoctorSave)), None
        )
        seer_checks = dict(self._seer_checks)
        if kill_votes:
            # break the tie once so the staged beat and the actual kill always agree
            counts = Counter(kill_votes)
            top = max(counts.values())
            kill_votes = [self._rng.choice(sorted(t for t, c in counts.items() if c == top))]
        # beats and resolutions must interleave, so the seer loop runs at reveal
        self._beat_kill_votes = kill_votes
        self._beat_save = save
        self._beat_seer_checks = seer_checks
        self._beat_plan = self._plan_night_beats(kill_votes, save, seer_checks)
        if self._beat_plan:
            self._open_window("reveal")
            return
        self._finish_night(kill_votes, save, seer_checks)

    def _plan_night_beats(
        self, kill_votes: list[int], save: int | None, seer_checks: dict[int, int]
    ) -> list[dict[str, Any]]:
        """Audience-only night reveal; skips abstained or dead roles."""
        plan: list[dict[str, Any]] = []
        if kill_votes:
            plan.append({"step": "wolves", "target": kill_votes[0]})
        if save is not None:
            plan.append({"step": "doctor", "target": save})
        for seer, target in seer_checks.items():
            role: Role = "werewolf" if target in self._frames else self._roles[target]
            plan.append({"step": "seer", "seer": seer, "target": target, "role": role})
        return plan

    def advance_reveal(self) -> None:
        """Emit the next staged beat; the table calls this on the hold clock."""
        if self.phase != "reveal":
            raise IllegalAction("not_your_turn", "no reveal in progress")
        step = self._beat_plan.pop(0)
        if "vote" in step:
            b = step["vote"]
            self._beat_tally[b["target"]] += b["weight"]
            self._emit(
                "ballot_reveal",
                {
                    "seat": b["seat"],
                    "target": b["target"],
                    "weight": b["weight"],
                    "tally": {str(t): c for t, c in sorted(self._beat_tally.items())},
                },
                audience=frozenset(),
            )
            if not self._beat_plan:
                self._finish_vote(self._vote_ballots)
            return
        self._emit("night_beat", step, audience=frozenset())
        if step["step"] == "seer":
            self._emit(
                "seer_result",
                {"seer": step["seer"], "seat": step["target"], "role": step["role"]},
                audience=frozenset({step["seer"]}),
            )
        if not self._beat_plan:
            self._finish_night(
                self._beat_kill_votes, self._beat_save, self._beat_seer_checks
            )

    def _finish_night(
        self, kill_votes: list[int], save: int | None, seer_checks: dict[int, int]
    ) -> None:
        for seer, target in seer_checks.items():
            role: Role = "werewolf" if target in self._frames else self._roles[target]
            if any(
                e.kind == "seer_result" and e.data.get("seer") == seer
                and e.day == self.day
                for e in self._events
            ):
                continue  # a seer beat already delivered this result
            self._emit(
                "seer_result",
                {"seer": seer, "seat": target, "role": role},
                audience=frozenset({seer}),
            )
        kill_target = None
        if kill_votes:
            counts = Counter(kill_votes)
            top = max(counts.values())
            tied = sorted(t for t, c in counts.items() if c == top)
            kill_target = self._rng.choice(tied)
        protected = kill_target is not None and (
            kill_target == save or kill_target in self._shields
        )
        killed = kill_target if kill_target is not None and not protected else None
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
        self._shields = set()  # a shield protects one night only
        self._check_end()
        if self.result is None and self.phase == "reveal":
            # the staged beat ends the night; dawn follows the last beat
            self._start_day()
            self._open_window("day_discussion")

    def _resolve_vote(self) -> None:
        ballots = []
        for s in self._pending:
            target = self._ballots[s].target if s in self._ballots else None
            weight = 2 if s in self._double_voters and target is not None else 1
            ballots.append({"seat": s, "target": target, "weight": weight})
        reveal = [b for b in ballots if b["target"] is not None]
        self._rng.shuffle(reveal)
        if reveal:
            # stage the ballot reveals on the reveal clock; tally and death follow
            self._vote_ballots = ballots
            self._beat_plan = [{"vote": b} for b in reveal]
            self._beat_tally = Counter()
            self._open_window("reveal")
            return
        self._finish_vote(ballots)

    def _finish_vote(self, ballots: list[dict[str, Any]]) -> None:
        tally: Counter[int] = Counter()
        for b in ballots:
            if b["target"] is not None:
                tally[b["target"]] += b["weight"]
        eliminated = None
        if tally:
            top = max(tally.values())
            tied = sorted(t for t, c in tally.items() if c == top)
            # a tie eliminates nobody, unlike the night's seeded tie-break
            if len(tied) == 1:
                eliminated = tied[0]
        result = {"ballots": ballots, "eliminated": eliminated}
        silent = self._silent_seats()
        if silent:
            result["silent"] = silent
        self._emit("vote_tally", result)
        if eliminated is not None:
            self._kill(eliminated, "vote")
        else:
            self._emit("no_death", {"cause": "vote"})
        self._check_end()
        if self.result is None and self.phase == "reveal":
            self._finish_day()

    def _start_day(self) -> None:
        self._posts_today = Counter()
        self._post_times = {}
        self._extra_posts = {}
        self._nominations = {}
        self._nominated_today = set()
        self._seconded_today = set()
        self._wakened = {}
        self._standing = []
        self._defence_queue = []
        self._reactions = {}
        self._reactions_today = Counter()
        self._reacted_today = {}
        self._reply_grants = {}
        self._replied_to = {}
        self._day_cursors = {s: len(self._posts) for s in self.alive()}

    def hot_seat_pick(self, exclude: set[int]) -> int | None:
        """Quietest living seat not yet hot today; RNG breaks the tie. Seeded."""
        candidates = [
            s
            for s in self.alive()
            if s not in exclude and self.posts_left(s) > 0
        ]
        if not candidates:
            return None
        fewest = min(self._posts_today.get(s, 0) for s in candidates)
        tied = sorted(s for s in candidates if self._posts_today.get(s, 0) == fewest)
        return self._rng.choice(tied)

    def _open_window(self, phase: Phase | None = None, defence_seat: int | None = None) -> None:
        if phase is not None:
            self.phase = phase
        self.round = 1 if self.phase == "day_discussion" else 0
        self._acted = set()
        self._ballots = {}
        if self.phase == "night":
            pending: dict[int, Obligation] = {}
            self._seer_checks = {}
            self._frames = {}
            self._beat_plan = []
            # shields persist from the day into the night; cleared after it resolves
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
            self._pending = {}
        elif self.phase == "reveal":
            self._pending = {}
        elif self.phase == "defence":
            assert defence_seat is not None
            self._pending = {defence_seat: "defence"}
            self._emit("defence", {"seat": defence_seat})
        elif self.phase == "day_vote":
            self._pending = {s: "vote" for s in self.alive()}
        suffix = f"defence:{defence_seat}" if defence_seat is not None else str(self.round)
        self.turn_id = f"{self.phase}:{self.day}:{suffix}"
        self._emit(
            "phase",
            {
                "phase": self.phase,
                "day": self.day,
                "round": self.round,
                "turn_id": self.turn_id,
                **({"seat": defence_seat} if defence_seat is not None else {}),
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
