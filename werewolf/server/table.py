"""One table: a sequence of seeded games under one lock, with budget and clock."""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from werewolf.engine import (
    Config,
    DoctorSave,
    Event,
    Game,
    IllegalAction,
    NightKill,
    Post,
    SeerCheck,
    Vote,
)

TOOL_CALL_BUDGET = 10
ANNOUNCEMENT_KINDS = {
    "death", "no_death", "seer_result", "phase", "result", "vote_skipped",
}
DAY_PHASES = ("day_discussion", "defence", "day_vote")
DRAIN_S = 20.0  # floor drains for this long after discussion_s ends
NEWS_KINDS = {
    "posts_revealed", "reaction", "nomination", "seconded", "standing",
    "death", "phase", "hot_seat_pass",
}
HOT_SEAT_PROMPTS = [
    "Who do you trust least, and why?",
    "Defend your vote yesterday.",
    "Name one seat you think is innocent.",
    "What did you notice last night?",
    "Who is playing too quietly?",
]
DEFAULT_HOT_SEAT_FINE = 0.10  # fraction of the seat budget


def _is_transition(prev: str | None, new: str) -> bool:
    # True when the window crosses between night and day phases.
    if prev is None or prev == new:
        return False
    return (prev == "night") != (new == "night")

# Per-game counters, reset when a new game starts at the same table.
SINCE_KEYS = ("events", "wolf_chat", "board", "announce", "whisper", "news")


@dataclass
class FloorPost:
    seat: int
    post: dict[str, Any]
    kind: str  # "normal" | "reply" | "hot" | "defence"
    charged: float = 0.0
    grant: dict[str, Any] | None = None  # reply grant to restore if dropped


@dataclass
class SeatState:
    table: str
    seat: int
    team: str
    token: str
    spent_usd: float = 0.0
    broke_emitted: bool = False
    process_alive: bool = True  # finale: flipped False when the bot process exits
    house: str | None = None  # brain name when the seat is a house bot
    since: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in SINCE_KEYS}
    )
    llm_busy: bool = False


class Table:
    def __init__(
        self,
        name: str,
        config: Config,
        budget_usd: float,
        window_s: float,
        runs_dir: Path,
        tokens: list[str],
        teams: list[str],
        auto_restart: bool = True,
        seed_step: int = 7919,
        min_window_s: float = 0.0,
        transition_s: float = 0.0,
        transition_on_start: bool = True,
        floor_s: float = 4.0,
        hot_seat_s: float = 6.0,
        hot_seat_answer_s: float = 5.0,
        hot_seat_fine: float = DEFAULT_HOT_SEAT_FINE,
        reveal_s: float = 1.5,
        beat_s: float = 2.5,
        manual_start: bool = False,
    ) -> None:
        self.name = name
        self.config = config
        self.budget_usd = budget_usd
        self.window_s = window_s
        self.runs_dir = runs_dir
        self.auto_restart = auto_restart
        self.seed_step = seed_step
        self.min_window_s = min_window_s
        self.transition_s = transition_s
        self.transition_on_start = transition_on_start
        self.reveal_s = reveal_s
        self.beat_s = beat_s
        # manual_start waits in the lobby for /admin/start before game one opens
        self.manual_start = manual_start
        self.awaiting_start = manual_start
        self.game_counter = 0 if manual_start else 1  # game one opens at __init__
        self.lock = asyncio.Lock()
        self.cond = asyncio.Condition(self.lock)
        self.game = Game(config)
        self.game.floor_queued = True  # server floor owns release timing
        self._rng = random.Random(config.seed ^ 0x5EED)
        self._base_seed = config.seed
        self._game_seq = 0
        self.seats: list[SeatState] = [
            SeatState(name, s, teams[s], tokens[s]) for s in range(config.seats)
        ]
        self.window_opened = time.monotonic()
        self.ready_seats: set[int] = set(range(config.seats))
        self.ready_event = asyncio.Event()
        self.need_role_check = False  # finale only: game waits for get_my_role
        # freeze holds the clock and turn serving until readiness/replacement completes
        self.frozen = False
        self.idle = False  # between games: finished, next game not yet started
        self.traces: list[dict[str, Any]] = []
        self.sse_seq = 0
        self.sse_subs: list[asyncio.Queue[dict[str, Any]]] = []
        self._run_written = False
        self._served_turn: dict[int, str] = {}
        self._thoughts: list[dict[str, Any]] = []  # audience-only hole-cam lines
        self._clocked_turn: str | None = self.game.turn_id
        self._released_at = time.monotonic()
        self._prev_phase: str | None = None
        # game start counts as a transition so the projector shows a card first
        self._transition_until = (
            time.monotonic() + self.transition_s if transition_on_start else 0.0
        )
        # discussion floor: one voice at a time
        self.floor_s = floor_s
        self.hot_seat_s = hot_seat_s
        self.hot_seat_answer_s = hot_seat_answer_s
        self.hot_seat_fine_frac = hot_seat_fine
        self._floor_queue: list[FloorPost] = []
        self._floor_hold: float | None = None
        self._drain_deadline: float | None = None
        self._hot_active: dict[str, Any] | None = None
        self._hot_today: set[int] = set()
        self._hot_day = 0
        self._dead_air_since = time.monotonic()
        self._speaking_seat: int | None = None
        self._cut_offs: list[dict[str, Any]] = []  # public, emit at day end
        self._hot_passes: list[dict[str, Any]] = []
        self._reveal_until = 0.0  # staged ballot/night-beat clock
        self._game_started = False
        if manual_start:
            # The constructor's game is a placeholder; the lobby holds until start.
            self.idle = True
            self._run_written = True
            self.frozen = True
            self.need_role_check = True
            self.ready_seats = set()
            self.ready_event.clear()

    # --- game lifecycle ------------------------------------------------------

    def start_game_locked(self, seed: int | None = None, need_role_check: bool = False) -> None:
        """Open a fresh game; caller holds the lock."""
        if seed is None:
            if self.manual_start and not self._game_started:
                seed = self._base_seed  # lobby start replays the pinned seed
            else:
                self._game_seq += 1
                seed = self._base_seed + self.seed_step * self._game_seq
        self.game_counter += 1
        self._run_written = False
        self._served_turn = {}
        self._thoughts = []
        self._floor_queue = []
        self._floor_hold = None
        self._speaking_seat = None
        self._drain_deadline = None
        self._hot_active = None
        self._hot_today = set()
        self._hot_day = 0
        self._cut_offs = []
        self._hot_passes = []
        self._game_started = False
        self.game = Game(replace(self.config, seed=seed))
        self.game.floor_queued = True  # server floor owns release timing
        self.traces = []
        self.window_opened = time.monotonic()
        self.idle = False
        self.awaiting_start = False
        # Every new game waits for each seat to call get_my_role once.
        self.need_role_check = True
        self.frozen = True
        self.ready_seats = set()
        self.ready_event.clear()
        for st in self.seats:
            st.spent_usd = 0.0
            st.broke_emitted = False
            st.since = {k: 0 for k in SINCE_KEYS}
        self.sse_seq = 0
        self._clocked_turn = self.game.turn_id
        self._released_at = time.monotonic()
        self._prev_phase = None
        self._reveal_until = 0.0
        # manual-start always lets the operator open the first window
        self._transition_until = (
            time.monotonic() + self.transition_s if self.transition_on_start else 0.0
        )
        if not need_role_check:
            # The constructor already clocks game1; later games re-clock here.
            self.note_window_locked()
        self._push_sse_locked(self.spectator_init())
        self._notify_sse_locked()

    def mark_ready_locked(self, seat: int) -> None:
        """A seat called get_my_role; in the lobby that only reports readiness."""
        self.ready_seats.add(seat)
        self._push_sse_locked(self.lobby_state())

    def begin_locked(self) -> None:
        """Operator start: unfreeze the ready game; caller holds the lock."""
        self.unfreeze_locked()
        self._game_started = True
        self._push_sse_locked({"kind": "game_start", "game": self.game_counter})
        self.cond.notify_all()

    def lobby_state(self) -> dict[str, Any]:
        return {
            "kind": "lobby",
            "seats": [
                {
                    "seat": s,
                    "team": st.team,
                    "house": st.house,
                    "ready": s in self.ready_seats,
                }
                for s, st in enumerate(self.seats)
            ],
        }

    def unfreeze_locked(self) -> None:
        """Open the held first window with a fresh deadline; caller holds the lock."""
        if not self.frozen or self.awaiting_start:
            return
        self.frozen = False
        self.need_role_check = False
        self.ready_seats = set(range(self.config.seats))
        self.ready_event.set()
        self._clocked_turn = self.game.turn_id
        self.window_opened = time.monotonic()
        self._released_at = time.monotonic()
        self._push_sse_locked(self.spectator_tick())

    def public_turn_id(self) -> str:
        # Game-scoped so a stale id from a previous game is never accepted.
        return f"g{self.game_counter}:{self.game.turn_id}"

    def budget_broke(self, seat: int) -> bool:
        return self.seats[seat].spent_usd >= self.budget_usd

    def is_broke(self, seat: int) -> bool:
        st = self.seats[seat]
        return not st.process_alive or st.spent_usd >= self.budget_usd

    def seat_view(self, seat: int) -> dict[str, Any]:
        """Public identity info for spectators; roles are omniscient-only."""
        g = self.game
        view = {
            "seat": seat,
            "team": self.seats[seat].team,
            "role": g.role(seat),
            "alive": seat in g.alive(),
            "broke": self.is_broke(seat),
            "spend": self.seats[seat].spent_usd,
        }
        if self.seats[seat].house:
            view["house"] = self.seats[seat].house
        return view

    # --- clock ---------------------------------------------------------------

    def release_at(self) -> float:
        # Held until readiness, min dwell, and any night/day transition pause pass.
        if self.frozen:
            return float("inf")
        return max(
            self._released_at,
            self.window_opened + self.min_window_s,
            self._transition_until,
        )

    def transition_active(self) -> bool:
        return not self.frozen and time.monotonic() < self._transition_until

    def window_length(self) -> float:
        """Discussion/defence get engine windows; night/vote use the configured cap."""
        if self.game.phase in ("day_discussion", "defence"):
            return self.game.window_s()
        return self.window_s

    def deadline(self) -> float:
        return self.release_at() + self.window_length()

    def holding(self) -> bool:
        return self.frozen or time.monotonic() < self.release_at()

    async def check_clock_locked(self) -> None:
        """Caller holds the lock; opens/clock-advances windows, applies fallback."""
        g = self.game
        self.note_window_locked()
        now = time.monotonic()
        if g.phase == "day_discussion" and g.result is None and not self.frozen:
            if self._drain_deadline is not None and now >= self._drain_deadline:
                pass  # drain expired: the deadline branch cuts off, nothing more releases
            else:
                self._pump_floor_locked()
            if self.holding():
                self._dead_air_since = now  # nobody can answer during a hold
            else:
                self._check_hot_seat_locked(now)
        if (
            not self.frozen
            and self._reveal_until
            and now >= self._reveal_until
        ):
            # staged ballot reveals and night beats step on the reveal clock
            if g.phase == "reveal":
                g.advance_reveal()
            self._reveal_until = 0.0
            self.note_window_locked()
            if not self._reveal_until and g.phase == "reveal" and g.result is None:
                # same phase, next beat: start a fresh reveal hold
                self._reveal_until = time.monotonic() + max(self._reveal_hold_s(), 0.001)
        elif (
            g.result is None
            and not self.frozen
            and not self._reveal_until
            and now >= self.deadline()
        ):
            if g.phase == "day_discussion":
                self._hot_active = None
                if self._drain_deadline is None:
                    self._drain_deadline = now + DRAIN_S
                if self._floor_queue and now < self._drain_deadline:
                    # drain: keep releasing every floor_s for up to DRAIN_S
                    self._pump_floor_locked()
                else:
                    self._cut_off_and_close_locked()
            elif g.phase == "day_vote":
                for seat in list(g.pending()):
                    self._fallback_locked(seat)
            elif g.phase == "night":
                self._resolve_night_locked()
            elif g.phase != "reveal":
                for seat in list(g.pending()):
                    self._fallback_locked(seat)
            self.note_window_locked()
        self._write_run_if_done_locked()
        if (
            self.auto_restart
            and not self.manual_start
            and not self.frozen
            and self.game.result is not None
            and not self.idle
        ):
            # Mark the gap; the next game opens on get_my_role readiness.
            self.idle = True
        if (
            self.manual_start
            and self._game_started
            and self.game.result is not None
            and not self.idle
        ):
            self.idle = True
            self.awaiting_start = True

    def _resolve_night_locked(self) -> None:
        """Advance every night obligation so the beat plan can be computed."""
        g = self.game
        pending = list(g.pending())
        if not pending:
            return  # already resolved; reveal beats step via check_clock_locked
        for seat in pending:
            self._fallback_locked(seat)

    def _write_run_if_done_locked(self) -> None:
        """Persist a finished game before any restart; caller holds the lock."""
        if self.game.result is not None and not self._run_written:
            try:
                self.write_run()
            except Exception:  # never lose the fact the game ended
                import traceback

                traceback.print_exc()
            self._run_written = True
            self._notify_sse_locked()  # the terminal death must reach viewers before game_over
            summary = self.build_summary()
            self._push_sse_locked(
                {
                    "kind": "game_over",
                    "game": self.game_counter,
                    "result": self.game.result,
                    "summary": summary,
                }
            )
            if self.manual_start:
                # the table parks in the lobby until the next /admin/start
                self.idle = True
                self.awaiting_start = True

    def build_summary(self) -> dict[str, Any]:
        """Audience-facing game recap; persisted with the run."""
        g = self.game
        winners = sorted(s for s in g.alive() if (s in g._wolves) == (g.result == "wolves"))
        accusations: Counter[int] = Counter()
        powerups = 0
        deaths: list[dict[str, Any]] = []
        for e in g.events(None):
            if e.kind == "death":
                deaths.append({"seat": e.data["seat"], "day": e.day, "cause": e.data["cause"]})
            elif e.kind == "posts_revealed":
                for p in e.data["posts"]:
                    if p.get("accuse") is not None:
                        accusations[p["accuse"]] += 1
            elif e.kind == "powerup":
                powerups += 1
        broke = [s for s in range(g.config.seats) if self.is_broke(s)]
        return {
            "winners": winners,
            "wolves": sorted(g._wolves),
            "deaths": deaths,
            "spend_total": round(sum(self.seats[s].spent_usd for s in range(g.config.seats)), 6),
            "broke_seats": broke,
            "most_accused": accusations.most_common(1)[0][0] if accusations else None,
            "powerups": powerups,
        }

    def _reveal_hold_s(self) -> float:
        """Votes pace at reveal_s, night beats at beat_s."""
        plan = self.game._beat_plan
        if plan and "vote" in plan[0]:
            return self.reveal_s
        return self.beat_s

    def note_window_locked(self) -> None:
        """Start the window deadline the moment the engine's turn_id changes."""
        if self.game.turn_id != self._clocked_turn:
            self._clocked_turn = self.game.turn_id
            self.window_opened = time.monotonic()
            if _is_transition(self._prev_phase, self.game.phase):
                self._transition_until = self.window_opened + self.transition_s
            if self.game.phase == "defence":
                self._floor_hold = None  # defence post releases on acceptance
            if self.game.phase not in ("day_discussion", "defence"):
                self._hot_active = None
            if self.game.phase == "day_discussion":
                self._dead_air_since = self.window_opened
            self._prev_phase = self.game.phase
            if self.game.phase == "reveal":
                # the staged reveal holds the next window
                # a zero hold still needs a nonzero mark or the reveal never steps
                hold = max(self._reveal_hold_s(), 0.001)
                self._reveal_until = time.monotonic() + hold
                self._transition_until = max(
                    self._transition_until, time.monotonic() + hold
                )

    # --- the discussion floor -------------------------------------------------

    @property
    def _floor_speaking(self) -> int | None:
        return self._speaking_seat

    def submit_discussion_post_locked(
        self, seat: int, action: Post, charge: float
    ) -> dict[str, Any]:
        """Validate through the engine, then queue instead of publishing."""
        g = self.game
        now = time.monotonic()
        if self.is_broke(seat):
            return {"error": "broke", "message": "seat budget exhausted"}
        if self._drain_deadline is not None or now >= self.deadline():
            return {"error": "stale_turn", "message": "discussion deadline passed"}
        if self.phase_flags(seat):
            return {"error": "still_queued"}
        if self._speaking_seat == seat and self._floor_hold is not None:
            if time.monotonic() < self._floor_hold:
                return {"error": "still_queued"}
        is_hot = self._hot_active is not None and self._hot_active["seat"] == seat
        if is_hot and now >= self._hot_active["deadline"]:
            # The answer window has shut: the fine lands and this is a normal post.
            self._hot_pass_locked()
            is_hot = False
            if self.phase_flags(seat):
                return {"error": "still_queued"}
        grant = g.reply_owed(seat) if action.reply_to is not None else None
        try:
            if is_hot:
                # In time: free, outside max_posts and the 5s gap.
                g._check_seat(seat)
                if seat not in g.alive():
                    raise IllegalAction("dead_seat")
                g._validate_post(seat, action, "defence", now)
                post = g._record_post(seat, action, now)
                g._posts_today[seat] = g._posts_today.get(seat, 0) - 1
                post["is_reply"] = True  # so the drop never refunds a count
                g.take_pending_release(seat)
            else:
                g.act(seat, action, now=now)
                post = g.take_pending_release(seat)
        except IllegalAction as e:
            return {"error": e.code, "message": e.msg}
        assert post is not None
        is_reply = bool(post.get("is_reply")) and not is_hot
        kind = "hot" if is_hot else "reply" if is_reply else "normal"
        entry = FloorPost(seat, post, kind, charged=charge, grant=grant)
        position = self._enqueue_locked(entry)
        if charge:
            self.charge_locked(seat, charge)
        return {"ok": True, "queued": position}

    def seat_queued(self, seat: int) -> bool:
        return self.phase_flags(seat)

    def _enqueue_locked(self, entry: FloorPost) -> int:
        """Replies and hot-seat answers jump the queue, behind the speaker."""
        now = time.monotonic()
        speaking = self._floor_hold is not None and now < self._floor_hold
        if entry.kind in ("reply", "hot") and (speaking or self._floor_queue):
            self._floor_queue.insert(0, entry)
            position = 0
        else:
            position = len(self._floor_queue)
            self._floor_queue.append(entry)
        self._pump_floor_locked()
        return position

    def phase_flags(self, seat: int) -> bool:
        return any(e.seat == seat for e in self._floor_queue)

    def _unemit_locked(self, kind: str) -> None:
        g = self.game
        for e in reversed(g._events):
            if e.kind == kind:
                g._events.remove(e)
                return

    def _pump_floor_locked(self) -> None:
        """Release the next queued post once the floor opens; caller holds lock."""
        now = time.monotonic()
        changed = False
        if self._floor_queue and (
            self._floor_hold is None or now >= self._floor_hold
        ):
            entry = self._floor_queue.pop(0)
            self.game.release_post(entry.seat, entry.post)
            # Release counts as the seat's last post time for the 5s gap.
            if not entry.post.get("is_reply") and entry.kind != "hot":
                self.game._post_times[entry.seat] = now
            self._floor_hold = now + self.floor_s
            self._speaking_seat = entry.seat
            self._dead_air_since = now
            if (
                entry.kind == "hot"
                and self._hot_active
                and self._hot_active["seat"] == entry.seat
            ):
                self._hot_active = None
            changed = True
        if changed or self._speaking_expired(now):
            self._push_sse_locked(self._floor_payload())

    def _speaking_expired(self, now: float) -> bool:
        if self._floor_hold is None or self._speaking_seat is None:
            return False
        if now < self._floor_hold:
            return False
        self._speaking_seat = None
        self._floor_hold = None
        return True

    def _floor_payload(self) -> dict[str, Any]:
        return {
            "kind": "floor",
            "speaking": self._speaking_seat,
            "queue": [e.seat for e in self._floor_queue],
        }

    def _cut_off_and_close_locked(self) -> None:
        """End of drain: drop what's left, then close the discussion window."""
        self._drain_deadline = None
        dropped = self.drop_floor_locked()
        if dropped:
            self._cut_offs.append({"kind": "cut_off", "seats": sorted(dropped)})
        self.game.close_discussion()
        self._flush_day_end_events_locked()

    def hot_seat_prompt_locked(self, seat: int) -> dict[str, Any] | None:
        """The open hot-seat obligation for wait_for_turn/read_board."""
        hot = self._hot_active
        if hot is None or hot["seat"] != seat:
            return None
        return {
            "prompt": hot["prompt"],
            "deadline_s": round(max(0.0, hot["deadline"] - time.monotonic()), 2),
        }

    def _flush_day_end_events_locked(self) -> None:
        """Cut-offs and hot-seat passes become public when the window closes."""
        g = self.game
        for c in self._cut_offs:
            g._emit("cut_off", {"seats": c["seats"]})
        for p in self._hot_passes:
            g._emit("hot_seat_pass", {"seat": p["seat"], "fine_usd": p["fine_usd"]})
        self._cut_offs = []
        self._hot_passes = []

    # --- the hot seat ----------------------------------------------------------

    def _floor_busy(self, now: float) -> bool:
        speaking = self._floor_hold is not None and now < self._floor_hold
        return speaking or bool(self._floor_queue)

    def _check_hot_seat_locked(self, now: float) -> None:
        """Dead air in discussion: put the quietest seat on the hot seat."""
        if self._drain_deadline is not None:
            return  # never during the drain
        if now < self.deadline():
            pass  # normal window; deadline() check is the drain trigger
        if self._hot_active is not None:
            # A queued/answered post by the hot seat is handled at release time.
            if now >= self._hot_active["deadline"]:
                self._hot_pass_locked()
            else:
                return
            if self._floor_busy(now) or now - self._dead_air_since < self.hot_seat_s:
                return
        elif self._floor_busy(now):
            self._dead_air_since = now
            return
        if now >= self.deadline():
            return  # the drain owns the floor now
        if now - self._dead_air_since < self.hot_seat_s:
            return
        if self._hot_day != self.game.day:
            self._hot_day = self.game.day
            self._hot_today = set()
        exclude = set(self._hot_today)
        seat = self.game.hot_seat_pick(exclude)
        if seat is None and exclude:
            # everyone has been hot: restart the rotation
            self._hot_today = set()
            seat = self.game.hot_seat_pick(set())
        if seat is None:
            return
        prompt = self._rng.choice(HOT_SEAT_PROMPTS)
        fine = round(self.hot_seat_fine_frac * self.budget_usd, 6)
        self._hot_today.add(seat)
        self._hot_active = {
            "seat": seat,
            "prompt": prompt,
            "fine": fine,
            "deadline": now + self.hot_seat_answer_s,
        }
        self._push_sse_locked({
            "kind": "hot_seat",
            "seat": seat,
            "prompt": prompt,
            "deadline_s": self.hot_seat_answer_s,
            "fine_usd": fine,
        })
        # No fresh dead-air credit for being picked: the floor was already empty.
        self._dead_air_since = now

    def _hot_pass_locked(self) -> None:
        hot = self._hot_active
        self._hot_active = None
        if hot is None:
            return
        seat = hot["seat"]
        fine = hot["fine"]
        charged = 0.0
        if not self.is_broke(seat):
            # Charge through the same ledger; it can flip the seat broke.
            self.charge_locked(seat, fine)
            charged = fine
        payload = {"seat": seat, "fine_usd": charged}
        self._hot_passes.append({"kind": "hot_seat_pass", **payload})
        self._push_sse_locked({"kind": "hot_seat_pass", **payload})
        self._dead_air_since = time.monotonic()

    def drop_floor_locked(self) -> list[int]:
        """Discussion over: drop queued posts, refund, restore grants."""
        if not self._floor_queue:
            self._floor_hold = None
            self._speaking_seat = None
            return []
        seats = [e.seat for e in self._floor_queue]
        g = self.game
        for e in self._floor_queue:
            if e.charged:
                self.charge_locked(e.seat, -e.charged)
            if not e.post.get("is_reply") and e.kind != "hot" and e.post["day"] == g.day:
                g._posts_today[e.seat] = g._posts_today.get(e.seat, 0) - 1
                g._post_times.pop(e.seat, None)
            if e.post["nominate"] is not None:
                g._nominated_today.discard(e.seat)
                nom = g._nominations.get(e.post["nominate"])
                if nom is not None and nom["by"] == e.seat and not nom.get("public"):
                    # never announced: it and any seconds behind it vanish with the cut
                    del g._nominations[e.post["nominate"]]
            if e.post["second"] is not None:
                g._seconded_today.discard(e.seat)
                nom = g._nominations.get(e.post["second"])
                if nom is not None:
                    nom["seconds"].discard(e.seat)
            if e.grant is not None:
                g._reply_grants.setdefault(e.seat, {})[e.grant["from"]] = e.grant
        self._floor_queue = []
        self._floor_hold = None
        self._speaking_seat = None
        self._push_sse_locked({"kind": "cut_off", "seats": sorted(seats)})
        self._push_sse_locked(self._floor_payload())
        return seats

    def _fallback_locked(self, seat: int) -> None:
        t0 = time.monotonic()
        g = self.game
        obligation = g.pending().get(seat)
        if obligation is None:
            return
        broke = self.is_broke(seat)
        if not broke:
            g.abstain(seat)
            if obligation in ("night_kill", "seer_check", "doctor_save"):
                g._emit(
                    "night_abstain",
                    {"seat": seat, "role": g.role(seat)},
                    audience=frozenset(),
                )
            self._trace(
                seat, "_fallback_abstain", {}, "ok",
                (time.monotonic() - t0) * 1000,
            )
            return
        rng = self._rng
        abstained = False
        try:
            if obligation == "vote":
                targets = g.legal_targets(seat)
                if targets:
                    g.act(seat, Vote(rng.choice(targets)))
                else:
                    g.abstain(seat)  # no standing nominees: no fallback ballot
                    abstained = True
            elif obligation == "seer_check":
                targets = g.legal_targets(seat)
                if targets:
                    g.act(seat, SeerCheck(rng.choice(targets)))
                else:
                    g.abstain(seat); abstained = True
            elif obligation == "doctor_save":
                targets = g.legal_targets(seat)
                if targets:
                    g.act(seat, DoctorSave(rng.choice(targets)))
                else:
                    g.abstain(seat); abstained = True
            elif obligation == "night_kill":
                wolves_alive = [
                    s
                    for s in g.alive()
                    if g.role(s) == "werewolf"
                ]
                funded = [s for s in wolves_alive if not self.is_broke(s)]
                if funded:
                    g.abstain(seat)  # a funded wolf still decides the kill
                    abstained = True
                else:
                    # one random ballot total: the lowest-seat wolf submits it
                    submitter = min(wolves_alive)
                    if seat == submitter:
                        targets = g.legal_targets(seat)
                        if targets:
                            g.act(seat, NightKill(rng.choice(targets)))
                        else:
                            g.abstain(seat); abstained = True
                    else:
                        g.abstain(seat); abstained = True
            else:  # defence: a broke nominee's defence simply times out
                g.abstain(seat)
                abstained = True
            if abstained and obligation in ("night_kill", "seer_check", "doctor_save"):
                g._emit(
                    "night_abstain",
                    {"seat": seat, "role": g.role(seat)},
                    audience=frozenset(),
                )
        except IllegalAction:
            g.abstain(seat)
        self._trace(
            seat, "_fallback_broke", {"obligation": obligation}, "ok",
            (time.monotonic() - t0) * 1000,
        )

    # --- events / announcements ----------------------------------------------

    def announcements_locked(self, seat: int) -> list[dict[str, Any]]:
        st = self.seats[seat]
        events = self.game.events(seat)
        out = [
            e.to_dict()
            for e in events[st.since["announce"] :]
            if e.kind in ANNOUNCEMENT_KINDS
        ]
        st.since["announce"] = len(events)
        return out

    def whispers_locked(self, seat: int) -> list[dict[str, Any]]:
        st = self.seats[seat]
        events = self.game.events(seat)
        out = [
            e.to_dict()
            for e in events[st.since["whisper"] :]
            if e.kind == "whisper" and e.data.get("target") == seat
        ]
        st.since["whisper"] = len(events)
        return [{"from": w["seat"], "msg": w["msg"]} for w in out]

    def spectator_init(self) -> dict[str, Any]:
        return {
            "kind": "init",
            "seats": self.config.seats,
            "game": self.game_counter,
            "budget_usd": self.budget_usd,
            "seat_info": [self.seat_view(s) for s in range(self.config.seats)],
        }

    def spectator_tick(self) -> dict[str, Any]:
        g = self.game
        alive = set(g.alive())
        now = time.monotonic()
        release = self.release_at()
        # Frozen tables release at inf, which JSON can't carry; -1 means held indefinitely.
        held = -1.0 if release == float("inf") else max(0.0, round(release - now, 1))
        window = self.window_length()
        # The turn clock is paused during a hold: full window until release.
        remaining = window if held != 0 else max(
            0.0, round(self.deadline() - now, 1)
        )
        seats = []
        for s, st in enumerate(self.seats):
            entry: dict[str, Any] = {
                "seat": s, "spent_usd": round(st.spent_usd, 6),
                "broke": self.is_broke(s), "alive": s in alive,
            }
            if st.house:
                entry["house"] = st.house
            seats.append(entry)
        return {
            "kind": "tick",
            "game": self.game_counter,
            "remaining_s": remaining,
            "held_s": held,
            "transition": self.transition_active(),
            "lobby": self.awaiting_start,
            "window_s": window,
            "pending": sorted(g.pending()) if g.result is None else [],
            "thinking": [s for s, st in enumerate(self.seats) if st.llm_busy],
            "waiting_ready": (
                sorted(set(range(self.config.seats)) - self.ready_seats)
                if self.need_role_check else []
            ),
            "seats": seats,
        }

    def _push_sse_locked(self, payload: dict[str, Any]) -> None:
        for q in self.sse_subs:
            q.put_nowait(payload)

    def _notify_sse_locked(self) -> None:
        events = self.game.events(None)
        for e in events[self.sse_seq :]:
            payload = self._sse_event_payload(e)
            if payload is None:
                continue
            for q in self.sse_subs:
                q.put_nowait(payload)
        self.sse_seq = len(events)

    def _sse_event_payload(self, e: Event) -> dict[str, Any] | None:
        """Top-level SSE shape per engine event; beats never carry day/round."""
        if e.kind == "phase":
            return {"kind": "phase", **e.data, "day": e.day, "round": e.round}
        if e.kind in ("ballot_cast", "night_acted", "ballot_reveal", "night_beat", "thought"):
            return {"kind": e.kind, **e.data}
        if e.kind == "posts_revealed":
            return {"kind": "posts_revealed", "posts": e.data["posts"]}
        return {"kind": "event", **e.to_dict()}

    def spend_snapshot(self) -> dict[int, dict[str, Any]]:
        return {
            s: {"spent_usd": round(self.seats[s].spent_usd, 6), "broke": self.is_broke(s)}
            for s in range(self.config.seats)
        }

    # --- charge / broke --------------------------------------------------------

    def record_thought_locked(self, seat: int, reason: str | None) -> None:
        """Hole cam: one audience-only line, kept for the retro."""
        if not reason:
            return
        text = str(reason)[:200]
        entry = {"seat": seat, "day": self.game.day, "phase": self.game.phase, "text": text}
        self._thoughts.append(entry)
        self.game.record_reason(seat, text)

    def charge_locked(self, seat: int, usd: float) -> None:
        st = self.seats[seat]
        st.spent_usd += usd
        if not st.broke_emitted and self.budget_broke(seat):
            st.broke_emitted = True
            for q in self.sse_subs:
                q.put_nowait({"kind": "broke", "seat": seat})

    # --- tracing / game.json ----------------------------------------------------

    def _trace(
        self, seat: int, tool: str, args: dict[str, Any], code: str, latency_ms: float
    ) -> None:
        self.traces.append(
            {
                "ts": time.time(),
                "seat": seat,
                "tool": tool,
                "args": args,
                "result_code": code,
                "latency_ms": round(latency_ms, 2),
            }
        )

    def _game_events_json(self) -> list[dict[str, Any]]:
        g = self.game
        out: list[dict[str, Any]] = []
        for e in g.events(None):
            if e.kind in ("ballot_cast", "night_acted", "ballot_reveal", "night_beat", "thought"):
                out.append({"kind": e.kind, "day": e.day, **e.data})
            elif e.kind == "posts_revealed":
                for p in e.data["posts"]:
                    out.append(
                        {
                            "kind": "post",
                            "day": p["day"],
                            "round": p["round"],
                            "seat": p["seat"],
                            "chat": p["chat"],
                            "claimed_role": p["claimed_role"],
                            "accuse": p["accuse"],
                            "reply_to": p["reply_to"],
                            "nominate": p["nominate"],
                            "second": p["second"],
                        }
                    )
            elif e.kind == "vote_tally":
                for b in e.data["ballots"]:
                    if b["target"] is not None:
                        out.append(
                            {
                                "kind": "vote",
                                "day": e.day,
                                "seat": b["seat"],
                                "target": b["target"],
                                "weight": b.get("weight", 1),
                            }
                        )
            elif e.kind == "death":
                out.append({"kind": "death", "day": e.day, **e.data})
            elif e.kind == "seer_result":
                out.append(
                    {
                        "kind": "seer_check",
                        "day": e.day,
                        "seat": e.data["seer"],
                        "target": e.data["seat"],
                        "role": e.data["role"],
                    }
                )
            elif e.kind == "wolf_chat":
                out.append(
                    {
                        "kind": "wolf_chat",
                        "day": e.day,
                        "seat": e.data["seat"],
                        "msg": e.data["msg"],
                    }
                )
            elif e.kind in ("nomination", "seconded", "standing", "vote_skipped"):
                out.append({"kind": e.kind, "day": e.day, **e.data})
            elif e.kind == "powerup":
                out.append(
                    {
                        "kind": "powerup",
                        "day": e.day,
                        "seat": e.data["seat"],
                        "item": e.data["item"],
                        "target": e.data.get("target"),
                        "usd": e.data.get("usd"),
                    }
                )
            elif e.kind == "whisper":
                out.append({"kind": "whisper", "day": e.day, **e.data})
        return out

    def write_run(self) -> Path:
        g = self.game
        game_dir = self.runs_dir / self.name / f"game{self.game_counter}"
        game_dir.mkdir(parents=True, exist_ok=True)
        events = self._game_events_json()
        for s in range(g.config.seats):
            if self.is_broke(s):
                events.append({"kind": "broke", "seat": s})
        (game_dir / "game.json").write_text(
            json.dumps(
                {
                    "seats": g.config.seats,
                    "roles": {str(s): g.role(s) for s in range(g.config.seats)},
                    "result": g.result,
                    "house": {
                        str(s): self.seats[s].house
                        for s in range(g.config.seats)
                        if self.seats[s].house
                    },
                    "teams": {
                        str(s): self.seats[s].team for s in range(g.config.seats)
                    },
                    "events": events,
                    "thoughts": self._thoughts,
                    "summary": self.build_summary(),
                    "spend": {
                        str(s): round(self.seats[s].spent_usd, 6)
                        for s in range(g.config.seats)
                    },
                },
                indent=1,
            )
        )
        (game_dir / "trace.jsonl").write_text(
            "".join(json.dumps(t) + "\n" for t in self.traces)
        )
        return game_dir

    async def finish_and_maybe_restart(self) -> None:
        """Finale path: write the run once; no auto-restart in final games."""
        async with self.cond:
            self._write_run_if_done_locked()
            self.cond.notify_all()


async def ticker(table: Table) -> None:
    """1s clock: applies deadlines and marks finished games idle."""
    while True:
        # Wake at the release/reveal instant too, so held windows start on time.
        wake = table.release_at()
        if table._reveal_until:
            wake = min(wake, table._reveal_until)
        await asyncio.sleep(min(1.0, max(0.05, wake - time.monotonic())))
        try:
            async with table.cond:
                await table.check_clock_locked()
                table._notify_sse_locked()
                table._push_sse_locked(table.spectator_tick())
                table.cond.notify_all()
            if (
                table.auto_restart
                and not table.idle
                and table.game.result is not None
                and table._run_written
            ):
                await asyncio.sleep(0.2)  # let waiters observe the result
                async with table.cond:
                    # The next game opens only when its readiness rule is met.
                    if not table.idle and table.game.result is not None:
                        table.idle = True
                        table._push_sse_locked(table.spectator_tick())
                        table.cond.notify_all()
        except Exception:  # keep the clock alive
            import traceback

            traceback.print_exc()
