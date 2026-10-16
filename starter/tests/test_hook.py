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


def locked_fixture():
    """The fixture body: create evals/.locked, then restore any pre-existing one."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    preexisting = LOCK.exists()
    LOCK.touch()
    yield LOCK
    if preexisting:
        LOCK.touch()
    else:
        LOCK.unlink(missing_ok=True)


@pytest.fixture
def locked():
    yield from locked_fixture()


def test_fixture_restores_preexisting_lock():
    # With a pre-existing lock, the fixture teardown restores it, not deletes it.
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    preexisting = LOCK.exists()
    LOCK.touch()
    try:
        gen = locked_fixture()
        next(gen)
        assert LOCK.exists()
        with pytest.raises(StopIteration):
            next(gen)
        assert LOCK.exists()
    finally:
        if not preexisting:
            LOCK.unlink(missing_ok=True)


# --- unlocked (default): teams author scorers, agents may edit evals/ ---


def test_unlocked_allows_edit_under_evals():
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


def test_locked_blocks_make_silent_unlock(locked):
    assert run_hook(bash("make -s unlock")).returncode == 2


def test_locked_blocks_unlock_in_a_pipeline(locked):
    assert run_hook(bash("echo hi && make unlock")).returncode == 2


def test_locked_blocks_python_write_into_evals(locked):
    cmd = "python3 -c \"open('evals/gates.toml','w').write('')\""
    assert run_hook(bash(cmd)).returncode == 2


def test_locked_blocks_arbitrary_command_mentioning_evals(locked):
    assert run_hook(bash("find . -name evals -exec rm -rf {} +")).returncode == 2


def test_locked_blocks_tee_into_evals(locked):
    assert run_hook(bash("echo hacked | tee evals/gates.toml")).returncode == 2


def test_locked_allows_read_only_evals_commands(locked):
    for cmd in (
        "cat evals/gates.toml",
        "ls evals",
        "head -3 evals/gates.toml",
        "tail -3 evals/gates.toml",
        "grep score_ evals/gates.toml",
        "rg score_ evals",
        "wc -l evals/gates.toml",
        "less evals/gates.toml",
        "diff evals/gates.toml evals/gates.toml",
        "git diff evals/gates.toml",
        "git log --oneline evals/gates.toml",
        "git status evals/gates.toml",
    ):
        assert run_hook(bash(cmd)).returncode == 0, cmd


def test_locked_allows_make_eval_check_practice(locked):
    for cmd in ("make eval", "make check", "make practice",
                "make -s eval", "make eval RUNS=runs/practice SEAT=6",
                "make -C . check"):
        assert run_hook(bash(cmd)).returncode == 0, cmd


def test_locked_allows_python_m_evals(locked):
    for cmd in (
        "uv run python -m evals --runs runs/practice",
        "python -m evals",
        "python3 -m evals --seat 6",
        "cd starter && uv run python -m evals",
    ):
        assert run_hook(bash(cmd)).returncode == 0, cmd


def test_locked_blocks_make_with_other_targets(locked):
    assert run_hook(bash("make eval lock unlock")).returncode == 2


def test_locked_allows_make_with_bot_target(locked):
    assert run_hook(bash("make eval bot")).returncode == 0


def test_locked_allows_make_without_evals(locked):
    assert run_hook(bash("make bot")).returncode == 0


def test_locked_allows_bash_without_evals(locked):
    assert run_hook(bash("echo nothing to see here")).returncode == 0


def test_locked_normalises_dotted_paths(locked):
    for p in ("./evals/./scorers/./mine.py", "evals/../evals/gates.toml",
              "starter/evals/scorers/../gates.toml"):
        assert run_hook(edit(p)).returncode == 2, p


def test_preexisting_lock_survives_fixture(locked):
    # The fixture restores a pre-existing lock instead of deleting it.
    assert LOCK.exists()


def test_locked_allows_make_lock(locked):
    # Re-locking is idempotent and harmless.
    assert run_hook(bash("make lock")).returncode == 0
