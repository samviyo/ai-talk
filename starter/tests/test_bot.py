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
    from bot.agent import fallback_nominee, parse_opener

    alive = [0, 1, 2, 3]
    assert parse_opener('{"chat": "x", "nominate": 2}', alive, 0, partners=[2]) == ("x", None, None)
    for day in range(1, 6):
        assert fallback_nominee(alive, 0, day, partners=[2]) in (1, 3)
