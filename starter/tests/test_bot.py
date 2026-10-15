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
