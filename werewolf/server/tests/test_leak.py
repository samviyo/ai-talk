"""Role-leak test: no MCP response may name a role the viewer may not know.

Over seeded fake-LLM games, capture every MCP response body per seat and
assert no {seat, role} pair leaks that the engine's own audience rules hide
from that seat (own role, wolf partners, revealed deaths, own seer checks).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
from pathlib import Path

import pytest

from werewolf.housebots.runner import run_bot

from .conftest import start_server

BRAINS = ["random", "quiet", "accuser", "baseline", "random", "quiet", "baseline"]
ROLE_WORDS = ("werewolf", "seer", "doctor", "villager")
SEAT_ROLE = re.compile(
    r'\{[^{}]*"seat": (\d+)[^{}]*"role": "(werewolf|seer|doctor|villager)"[^{}]*\}'
)

_current_seat: contextvars.ContextVar[int] = contextvars.ContextVar("seat", default=-1)
_captured: list[list[str]] = []


async def _one_game(tmp_path: Path, seed: int) -> None:
    global _captured
    _captured = [[] for _ in range(7)]
    srv = await start_server(tmp_path, seed=seed, window_s=0.5)
    import werewolf.housebots.runner as runner

    real = runner._call

    async def capture(session, tool, args):
        result = await real(session, tool, args)
        seat = _current_seat.get()
        if seat >= 0:
            _captured[seat].append(json.dumps(result))
        return result

    runner._call = capture
    try:
        async def one(seat: int):
            _current_seat.set(seat)
            return await run_bot(srv.url, srv.token(seat), BRAINS[seat], seed + seat)

        results = await asyncio.wait_for(
            asyncio.gather(*(one(s) for s in range(7))), timeout=180
        )
        assert all(r.get("status") == "over" for r in results)
    finally:
        runner._call = real

    table = srv.state.tables[0]
    g = table.game
    deaths = {e.data["seat"] for e in g.events(None) if e.kind == "death"}
    wolves = {s for s in range(7) if g.role(s) == "werewolf"}
    seer_checks: dict[int, set[int]] = {}
    for e in g.events(None):
        if e.kind == "seer_result":
            seer_checks.setdefault(e.data["seer"], set()).add(e.data["seat"])

    def known(seat: int) -> set[int]:
        k = {seat} | deaths | seer_checks.get(seat, set())
        if seat in wolves:
            k |= wolves
        return k

    for seat in range(7):
        allowed = known(seat)
        for body in _captured[seat]:
            for m in SEAT_ROLE.finditer(body):
                target, role = int(m.group(1)), m.group(2)
                assert target in allowed, (
                    f"seed {seed}: seat {seat} learned seat {target} is {role}"
                )
    await srv.stop()


@pytest.mark.parametrize("seed", [101, 202, 303, 404, 505, 606, 707, 808, 909, 1010,
                                  1111, 1212, 1313, 1414, 1515, 1616, 1717, 1818, 1919, 2020])
async def test_no_role_leak(tmp_path: Path, seed: int):
    await _one_game(tmp_path, seed)
