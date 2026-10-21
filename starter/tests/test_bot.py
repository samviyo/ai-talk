"""Bot hygiene: tool-call artifacts must never reach the board as posts."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.agent import strip_artifacts  # noqa: E402


def test_strip_invoke_artifacts() -> None:
    assert strip_artifacts('<invoke name="wait_for_turn"></invoke>') == ""
    assert strip_artifacts('vote 4 <invoke name="x"></invoke>') == "vote 4"
    assert strip_artifacts("a normal post about seat 4") == "a normal post about seat 4"


def test_parse_opener_nominates_only_living_others() -> None:
    from bot.agent import parse_opener

    alive = [0, 1, 2, 3]
    assert parse_opener('{"chat": "hi", "nominate": 2, "second": null}', alive, 0) == ("hi", 2, None)
    assert parse_opener('Sure: {"chat": "x", "nominate": "3"} done', alive, 0) == ("x", 3, None)
    assert parse_opener('{"chat": "x", "nominate": 0, "second": 9}', alive, 0) == ("x", None, None)
    assert parse_opener("plain post", alive, 0) == ("plain post", None, None)


def test_wolves_never_nominate_partners() -> None:
    from bot.agent import parse_opener

    alive = [0, 1, 2, 3]
    assert parse_opener('{"chat": "x", "nominate": 2}', alive, 0, partners=[2]) == ("x", None, None)


def _run_once_raising(monkeypatch, exc: BaseException) -> bool:
    import bot.__main__ as main

    async def fake_play(*args) -> None:
        raise exc

    monkeypatch.setattr(main, "play", fake_play)
    return main._play_once("http://x", "tok", None, {})


def test_dropped_connections_reconnect_and_other_errors_raise(monkeypatch) -> None:
    import httpx
    import pytest
    from mcp.shared.exceptions import MCPError
    from mcp_types import CONNECTION_CLOSED, INTERNAL_ERROR

    closed = MCPError(code=CONNECTION_CLOSED, message="SSE stream ended without a response")
    # anyio task groups nest the real error, so the bot must look inside groups.
    assert _run_once_raising(monkeypatch, ExceptionGroup("tg", [ExceptionGroup("inner", [closed])])) is False
    assert _run_once_raising(monkeypatch, httpx.ReadTimeout("slow")) is False
    with pytest.raises(BaseExceptionGroup):
        _run_once_raising(monkeypatch, MCPError(code=INTERNAL_ERROR, message="bug"))
    with pytest.raises(BaseExceptionGroup):
        _run_once_raising(monkeypatch, ExceptionGroup("tg", [closed, ValueError("bug")]))


def test_resume_replays_an_open_turn_only() -> None:
    import time

    from bot.__main__ import _resume

    turn = {"turn_id": "t1", "phase": "day_vote", "day": 2, "deadline_s": 20}
    resumed = _resume({"turn": turn, "until": time.monotonic() + 8})
    assert resumed["turn_id"] == "t1" and 7 < resumed["deadline_s"] <= 8
    assert _resume({"turn": turn, "until": time.monotonic() + 0.5}) is None
    assert _resume({}) is None
