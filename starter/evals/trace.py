"""Loading and schema for game traces and game logs.

trace.jsonl: one JSON object per line: {ts, seat, tool, args, result_code, latency_ms}.
game.json: one object per game:

  {
    "seats": 7,
    "roles": {"0": "werewolf", "1": "seer", ...},   # seat -> role, string keys
    "result": "village" | "wolves" | "draw",
    "events": [                                      # engine-visible events, in order
      {"kind": "post", "day": 1, "round": 1, "seat": 2, "chat": "...", "claimed_role": null, "accuse": null},
      {"kind": "vote", "day": 1, "seat": 3, "target": 5},
      {"kind": "death", "day": 1, "seat": 5, "role": "werewolf", "cause": "vote" | "night"},
      {"kind": "seer_check", "day": 1, "seat": 1, "target": 5, "role": "werewolf"}
    ],
    "spend": {"0": 0.11, ...}                        # seat -> USD spent
  }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

PROTOCOL_ERROR_CODES = {
    "bad_schema",
    "not_your_turn",
    "invalid_target",
    "dead_seat",
    "stale_turn",
    "already_acted",
    "too_long",
    "call_budget_exceeded",
}


@dataclass
class TraceLine:
    ts: float
    seat: int
    tool: str
    args: dict
    result_code: str
    latency_ms: float


@dataclass
class GameLog:
    seats: int
    roles: dict[int, str]
    result: str
    events: list[dict]
    spend: dict[int, float]
    wolves: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.wolves = sorted(s for s, r in self.roles.items() if r == "werewolf")

    def dead_before(self, day: int) -> set[int]:
        return {e["seat"] for e in self.events if e["kind"] == "death" and e["day"] < day}


def load_game(game_dir: Path) -> tuple[list[TraceLine], GameLog]:
    traces = [
        TraceLine(
            ts=float(l["ts"]),
            seat=int(l["seat"]),
            tool=l["tool"],
            args=l.get("args", {}),
            result_code=l.get("result_code", "ok"),
            latency_ms=float(l.get("latency_ms", 0)),
        )
        for line in (game_dir / "trace.jsonl").read_text().splitlines()
        if line.strip()
        for l in [json.loads(line)]
    ]
    raw = json.loads((game_dir / "game.json").read_text())
    log = GameLog(
        seats=int(raw["seats"]),
        roles={int(k): v for k, v in raw["roles"].items()},
        result=raw["result"],
        events=raw.get("events", []),
        spend={int(k): float(v) for k, v in raw.get("spend", {}).items()},
    )
    return traces, log


def iter_games(runs_dir: Path):
    """Yield (name, traces, log) for each <table>/<game> pair; accepts a runs root or one table dir."""
    if (runs_dir / "trace.jsonl").exists():
        table_dirs = [runs_dir.parent]
    elif any((p / "trace.jsonl").exists() for p in runs_dir.iterdir() if p.is_dir()):
        table_dirs = [runs_dir]
    else:
        table_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir())
    for table_dir in table_dirs:
        for game_dir in sorted(p for p in table_dir.iterdir() if p.is_dir()):
            if (game_dir / "trace.jsonl").exists() and (game_dir / "game.json").exists():
                traces, log = load_game(game_dir)
                yield f"{table_dir.name}/{game_dir.name}", traces, log
