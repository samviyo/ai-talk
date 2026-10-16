"""Pipe sample PreToolUse hook JSON into the script; exit 2 means blocked."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

STARTER = Path(__file__).resolve().parent.parent
HOOK = STARTER / ".claude" / "hooks" / "protect-evals.sh"
LOCK = STARTER / "evals" / ".locked"


def run_hook(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(payload),
        capture_output=True, text=True,
    )


def edit(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}


def write(path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": path, "content": "x"}}


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.fixture
def locked():
    """Create evals/.locked for the test; always remove it after."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.touch()
    yield LOCK
    LOCK.unlink(missing_ok=True)


# --- unlocked (default): teams author scorers, agents may edit evals/ ---


def test_unlocked_allows_edit_under_evals():
    assert run_hook(edit("/repo/starter/evals/scorers/mine.py")).returncode == 0


def test_unlocked_allows_write_under_evals():
    assert run_hook(write("evals/scorers/new.py")).returncode == 0


def test_unlocked_allows_rm_via_bash():
    assert run_hook(bash("rm evals/scorers/old.py")).returncode == 0


def test_unlocked_allows_make_lock():
    assert run_hook(bash("make lock")).returncode == 0


def test_unlocked_allows_make_unlock():
    assert run_hook(bash("make unlock")).returncode == 0


def test_allows_edit_outside_evals(locked):
    assert run_hook(edit("/repo/starter/bot/agent.py")).returncode == 0


def test_allows_reading_evals(locked):
    assert run_hook(bash("cat evals/gates.toml")).returncode == 0


def test_allows_running_evals(locked):
    assert run_hook(bash("uv run python -m evals --runs runs/practice")).returncode == 0


def test_allows_unrelated_tool(locked):
    assert run_hook({"tool_name": "Read", "tool_input": {"file_path": "evals/harness.py"}}).returncode == 0


# --- locked (evals/.locked exists): evals/ is frozen for agents ---


def test_locked_blocks_edit_under_evals(locked):
    assert run_hook(edit("/repo/starter/evals/scorers/mine.py")).returncode == 2


def test_locked_blocks_edit_of_lock_file(locked):
    assert run_hook(edit(str(LOCK))).returncode == 2


def test_locked_blocks_write_under_evals(locked):
    assert run_hook(write("evals/scorers/new.py")).returncode == 2


def test_locked_blocks_rm_via_bash(locked):
    assert run_hook(bash("rm evals/scorers/mine.py")).returncode == 2


def test_locked_blocks_redirect_into_evals(locked):
    assert run_hook(bash("echo hacked > evals/gates.toml")).returncode == 2


def test_locked_blocks_sed_i(locked):
    assert run_hook(bash("sed -i '' s/x/y/ evals/harness.py")).returncode == 2


def test_locked_blocks_removing_locked(locked):
    assert run_hook(bash("rm evals/.locked")).returncode == 2


def test_locked_blocks_mv_of_locked(locked):
    assert run_hook(bash("mv evals/.locked /tmp/escape")).returncode == 2


def test_locked_blocks_make_unlock(locked):
    assert run_hook(bash("make unlock")).returncode == 2


def test_locked_allows_make_lock(locked):
    # Re-locking is idempotent and harmless.
    assert run_hook(bash("make lock")).returncode == 0
