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
    ) -> None:
        self.name = name
        self.config = config
        self.budget_usd = budget_usd
        self.window_s = window_s
        self.runs_dir = runs_dir
        self.auto_restart = auto_restart
        self.seed_step = seed_step
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
        self.traces: list[dict[str, Any]] = []
        self.sse_seq = 0
        self.sse_subs: list[asyncio.Queue[dict[str, Any]]] = []
        self._run_written = False
        self._served_turn: dict[int, str] = {}

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
        self.need_role_check = need_role_check
        self.ready_seats = set() if need_role_check else set(range(self.config.seats))
        self.ready_event.clear()
        for st in self.seats:
            st.spent_usd = 0.0
            st.broke_emitted = False
            st.since = {k: 0 for k in SINCE_KEYS}
        self._notify_sse_locked()

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

    def deadline(self) -> float:
        return self.window_opened + self.window_s

    async def check_clock_locked(self) -> None:
        """Caller holds the lock; opens/clock-advances windows, applies fallback."""
        g = self.game
        if g.turn_id != getattr(self, "_clocked_turn", None):
            self._clocked_turn = g.turn_id
            self.window_opened = time.monotonic()
        if g.result is None and time.monotonic() >= self.deadline():
            for seat in list(g.pending()):
                self._fallback_locked(seat)
            self._clocked_turn = g.turn_id
            self.window_opened = time.monotonic()
            if g.result is not None and not self._run_written:
                self._run_written = True
                self.write_run()

    def _fallback_locked(self, seat: int) -> None:
        g = self.game
        obligation = g.pending().get(seat)
        if obligation is None:
            return
        broke = self.is_broke(seat)
        if not broke:
            g.abstain(seat)
            self._trace(seat, "_fallback_abstain", {}, "ok", 0.0)
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
                    targets = g.legal_targets(seat)
                    g.act(seat, NightKill(rng.choice(targets))) if targets else g.abstain(seat)
            else:  # post: broke seats stay silent
                g.abstain(seat)
        except IllegalAction:
            g.abstain(seat)
        self._trace(seat, "_fallback_broke", {"obligation": obligation}, "ok", 0.0)

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
    """1s clock: applies deadlines and restarts finished games."""
    while True:
        await asyncio.sleep(1.0)
        try:
            async with table.cond:
                await table.check_clock_locked()
                table._notify_sse_locked()
                table.cond.notify_all()
            if (
                table.auto_restart
                and table.game.result is not None
                and table._run_written
            ):
                await asyncio.sleep(0.2)  # let waiters observe the result
                async with table.cond:
                    if table.game.result is not None and table._run_written:
                        table.start_game_locked()
                        table.cond.notify_all()
        except Exception:  # keep the clock alive
            import traceback

            traceback.print_exc()
