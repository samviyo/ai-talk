"""One table: a sequence of seeded games under one lock, with budget and clock."""

from __future__ import annotations

import asyncio
import json
import random
import time
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
ANNOUNCEMENT_KINDS = {"death", "no_death", "seer_result", "phase", "result"}
DAY_PHASES = ("day_discussion", "day_vote")


def _is_transition(prev: str | None, new: str) -> bool:
    # True when the window crosses between night and day phases.
    if prev is None or prev == new:
        return False
    return (prev == "night") != (new == "night")

# Per-game counters, reset when a new game starts at the same table.
SINCE_KEYS = ("events", "wolf_chat", "board", "announce")


@dataclass
class SeatState:
    table: str
    seat: int
    team: str
    token: str
    spent_usd: float = 0.0
    broke_emitted: bool = False
    process_alive: bool = True  # finale: flipped False when the bot process exits
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
        self.game_counter = 1  # the game opened in __init__ is game1
        self.lock = asyncio.Lock()
        self.cond = asyncio.Condition(self.lock)
        self.game = Game(config)
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
        self._clocked_turn: str | None = self.game.turn_id
        self._released_at = time.monotonic()
        self._prev_phase: str | None = None
        # game start counts as a transition so the projector shows a card first
        self._transition_until = (
            time.monotonic() + self.transition_s if transition_on_start else 0.0
        )

    # --- game lifecycle ------------------------------------------------------

    def start_game_locked(self, seed: int | None = None, need_role_check: bool = False) -> None:
        """Open a fresh game; caller holds the lock."""
        if seed is None:
            self._game_seq += 1
            seed = self._base_seed + self.seed_step * self._game_seq
        self.game_counter += 1
        self._run_written = False
        self._served_turn = {}
        self.game = Game(replace(self.config, seed=seed))
        self.traces = []
        self.window_opened = time.monotonic()
        self.idle = False
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
        self._transition_until = (
            time.monotonic() + self.transition_s if self.transition_on_start else 0.0
        )
        if not need_role_check:
            # The constructor already clocks game1; later games re-clock here.
            self.note_window_locked()
        self._push_sse_locked(self.spectator_init())
        self._notify_sse_locked()

    def unfreeze_locked(self) -> None:
        """Open the held first window with a fresh deadline; caller holds the lock."""
        if not self.frozen:
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
        return {
            "seat": seat,
            "team": self.seats[seat].team,
            "role": g.role(seat),
            "alive": seat in g.alive(),
            "broke": self.is_broke(seat),
            "spend": self.seats[seat].spent_usd,
        }

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

    def deadline(self) -> float:
        return self.release_at() + self.window_s

    def holding(self) -> bool:
        return self.frozen or time.monotonic() < self.release_at()

    async def check_clock_locked(self) -> None:
        """Caller holds the lock; opens/clock-advances windows, applies fallback."""
        g = self.game
        self.note_window_locked()
        if (
            g.result is None
            and not self.frozen
            and time.monotonic() >= self.deadline()
        ):
            for seat in list(g.pending()):
                self._fallback_locked(seat)
            self.note_window_locked()
        self._write_run_if_done_locked()
        if (
            self.auto_restart
            and not self.frozen
            and self.game.result is not None
            and not self.idle
        ):
            # Mark the gap; the next game opens on get_my_role readiness.
            self.idle = True

    def _write_run_if_done_locked(self) -> None:
        """Persist a finished game before any restart; caller holds the lock."""
        if self.game.result is not None and not self._run_written:
            try:
                self.write_run()
            except Exception:  # never lose the fact the game ended
                import traceback

                traceback.print_exc()
            self._run_written = True

    def note_window_locked(self) -> None:
        """Start the window deadline the moment the engine's turn_id changes."""
        if self.game.turn_id != self._clocked_turn:
            self._clocked_turn = self.game.turn_id
            self.window_opened = time.monotonic()
            if _is_transition(self._prev_phase, self.game.phase):
                self._transition_until = self.window_opened + self.transition_s
            self._prev_phase = self.game.phase

    def _fallback_locked(self, seat: int) -> None:
        t0 = time.monotonic()
        g = self.game
        obligation = g.pending().get(seat)
        if obligation is None:
            return
        broke = self.is_broke(seat)
        if not broke:
            g.abstain(seat)
            self._trace(
                seat, "_fallback_abstain", {}, "ok",
                (time.monotonic() - t0) * 1000,
            )
            return
        rng = self._rng
        try:
            if obligation == "vote":
                targets = g.legal_targets(seat)
                g.act(seat, Vote(rng.choice(targets))) if targets else g.abstain(seat)
            elif obligation == "seer_check":
                targets = g.legal_targets(seat)
                g.act(seat, SeerCheck(rng.choice(targets))) if targets else g.abstain(seat)
            elif obligation == "doctor_save":
                targets = g.legal_targets(seat)
                g.act(seat, DoctorSave(rng.choice(targets))) if targets else g.abstain(seat)
            elif obligation == "night_kill":
                wolves_alive = [
                    s
                    for s in g.alive()
                    if g.role(s) == "werewolf"
                ]
                funded = [s for s in wolves_alive if not self.is_broke(s)]
                if funded:
                    g.abstain(seat)  # a funded wolf still decides the kill
                else:
                    # one random ballot total: the lowest-seat wolf submits it
                    submitter = min(wolves_alive)
                    if seat == submitter:
                        targets = g.legal_targets(seat)
                        g.act(seat, NightKill(rng.choice(targets))) if targets else g.abstain(seat)
                    else:
                        g.abstain(seat)
            else:  # post: broke seats stay silent
                g.abstain(seat)
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
        held = max(0.0, round(self.release_at() - now, 1))
        # The turn clock is paused during a hold: full window until release.
        remaining = self.window_s if held > 0 else max(
            0.0, round(self.deadline() - now, 1)
        )
        return {
            "kind": "tick",
            "game": self.game_counter,
            "remaining_s": remaining,
            "held_s": held,
            "transition": self.transition_active(),
            "window_s": self.window_s,
            "pending": sorted(g.pending()) if g.result is None else [],
            "thinking": [s for s, st in enumerate(self.seats) if st.llm_busy],
            "waiting_ready": (
                sorted(set(range(self.config.seats)) - self.ready_seats)
                if self.need_role_check else []
            ),
            "seats": [
                {"seat": s, "spent_usd": round(st.spent_usd, 6),
                 "broke": self.is_broke(s), "alive": s in alive}
                for s, st in enumerate(self.seats)
            ],
        }

    def _push_sse_locked(self, payload: dict[str, Any]) -> None:
        for q in self.sse_subs:
            q.put_nowait(payload)

    def _notify_sse_locked(self) -> None:
        events = self.game.events(None)
        for e in events[self.sse_seq :]:
            payload = {"kind": "event", **e.to_dict()}
            for q in self.sse_subs:
                q.put_nowait(payload)
        self.sse_seq = len(events)

    def spend_snapshot(self) -> dict[int, dict[str, Any]]:
        return {
            s: {"spent_usd": round(self.seats[s].spent_usd, 6), "broke": self.is_broke(s)}
            for s in range(self.config.seats)
        }

    # --- charge / broke --------------------------------------------------------

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
            if e.kind == "posts_revealed":
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
                    "events": events,
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
            if self.game.result is not None and not self._run_written:
                self.write_run()
                self._run_written = True
                self._notify_sse_locked()
                self.cond.notify_all()


async def ticker(table: Table) -> None:
    """1s clock: applies deadlines and marks finished games idle."""
    while True:
        # Wake at the release instant too, so held windows start on time.
        await asyncio.sleep(min(1.0, max(0.05, table.release_at() - time.monotonic())))
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
