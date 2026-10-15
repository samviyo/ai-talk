"""Pipe sample PreToolUse hook JSON into the script; exit 2 means blocked."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "protect-evals.sh"


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


def test_blocks_edit_under_evals():
    assert run_hook(edit("/repo/starter/evals/checks.py")).returncode == 2


def test_blocks_write_under_evals():
    assert run_hook(write("evals/new_check.py")).returncode == 2


def test_blocks_rm_via_bash():
    assert run_hook(bash("rm evals/thresholds.toml")).returncode == 2


def test_blocks_redirect_into_evals():
    assert run_hook(bash("echo hacked > evals/thresholds.toml")).returncode == 2


def test_blocks_sed_i():
    assert run_hook(bash("sed -i '' s/x/y/ evals/checks.py")).returncode == 2


def test_allows_edit_outside_evals():
    assert run_hook(edit("/repo/starter/bot/agent.py")).returncode == 0


def test_allows_reading_evals():
    assert run_hook(bash("cat evals/thresholds.toml")).returncode == 0


def test_allows_running_evals():
    assert run_hook(bash("uv run python -m evals --runs runs")).returncode == 0


def test_allows_unrelated_tool():
    assert run_hook({"tool_name": "Read", "tool_input": {"file_path": "evals/checks.py"}}).returncode == 0
