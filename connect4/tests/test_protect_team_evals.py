"""Lock-layer tests through the real entry points: the PreToolUse hook and make."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from connect4 import evals

ROOT = Path(evals.__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "protect-team-evals.sh"
TEAM = ROOT / "connect4" / "team_evals"
LOCK = TEAM / ".locked"
PROTECTED = [TEAM / "gates.toml", *sorted(TEAM.glob("*.py"))]


def _save_lock() -> tuple[bool, bytes | None]:
    existed = LOCK.exists()
    return existed, LOCK.read_bytes() if existed else None


def _restore_lock(existed: bool, content: bytes | None) -> None:
    """Real locks go back; markers this test created are removed."""
    if existed:
        LOCK.write_bytes(content)
    else:
        LOCK.unlink(missing_ok=True)


@pytest.fixture
def locked():
    """Install .locked for the test; a real pre-existing lock is restored after."""
    existed, content = _save_lock()
    LOCK.write_bytes(b"")
    try:
        yield LOCK
    finally:
        _restore_lock(existed, content)


@pytest.fixture
def unlocked():
    """Remove .locked for the test; a real pre-existing lock is restored after."""
    existed, content = _save_lock()
    LOCK.unlink(missing_ok=True)
    try:
        yield
    finally:
        _restore_lock(existed, content)


def hook(tool: str, **tool_input) -> subprocess.CompletedProcess:
    """Feed one PreToolUse payload to the real hook script."""
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    return subprocess.run(["bash", str(HOOK)], input=payload,
                          capture_output=True, text=True)


def bash(command: str) -> subprocess.CompletedProcess:
    return hook("Bash", command=command)


@pytest.mark.usefixtures("locked")
@pytest.mark.parametrize("command", [
    "python3 -c \"open('connect4/team_evals/gates.toml','w').write('')\"",
    "python3 -c \"import pathlib; pathlib.Path('connect4/./team_evals/x.py').write_text('x')\"",
    "uv run python -c \"open('connect4/team_evals/s.py','w')\"",
    "rm connect4/team_evals/.locked",
    "rm -f connect4/team_evals/workshop_scorers.py",
    "mv connect4/team_evals/gates.toml /tmp/x",
    "chmod +w connect4/team_evals/gates.toml",
    "echo x > connect4/team_evals/gates.toml",
    "cat connect4/team_evals/gates.toml > connect4/team_evals/copy.toml",
    "cd connect4/team_evals && touch evil.py",
    "ls connect4/team_evals | xargs rm -f",
    "make -s unlock-c4",
    "make unlock-c4",
    "make -C . unlock-c4",
])
def test_writes_and_unlock_are_blocked_while_locked(command):
    r = bash(command)
    assert r.returncode == 2, f"{command}: {r.stderr}"


@pytest.mark.usefixtures("locked")
@pytest.mark.parametrize("command", [
    "cat connect4/team_evals/gates.toml",
    "less connect4/team_evals/gates.toml",
    "head connect4/team_evals/gates.toml",
    "tail connect4/team_evals/gates.toml",
    "grep min connect4/team_evals/gates.toml",
    "rg min connect4/team_evals/gates.toml",
    "ls connect4/team_evals",
    "wc -l connect4/team_evals/gates.toml",
    "diff connect4/team_evals/gates.toml connect4/team_evals/gates.toml",
    "git diff -- connect4/team_evals/gates.toml",
    "git log --oneline -- connect4/team_evals",
    "git status -- connect4/team_evals",
    "cat connect4/team_evals/gates.toml | grep min",
    "make eval-c4",
    "make eval-c4 BOT=connect4.bots.naive:choose TRIALS=2",
    "python -m connect4.evals --bot connect4.bots.naive:choose",
    "uv run python -m connect4.evals --puzzles 4 --games 2",
    "uv run pytest -q connect4",
])
def test_reads_and_eval_runs_pass_while_locked(command):
    r = bash(command)
    assert r.returncode == 0, f"{command}: {r.stderr}"


@pytest.mark.usefixtures("locked")
def test_unrelated_commands_pass_while_locked():
    assert bash("cat README.md").returncode == 0


@pytest.mark.usefixtures("locked")
def test_edit_paths_are_normalized_before_matching():
    straight = str(TEAM / "gates.toml")
    for twisted in (straight.replace("team_evals", "./team_evals"),
                    straight.replace("team_evals", "team_evals//."),
                    straight.replace("team_evals", "team_evals/../team_evals")):
        r = hook("Edit", file_path=twisted, old_string="a", new_string="b")
        assert r.returncode == 2, twisted
    assert hook("Edit", file_path=straight.replace("team_evals", "tests"),
                old_string="a", new_string="b").returncode == 0


@pytest.mark.usefixtures("locked")
def test_write_paths_are_normalized_before_matching():
    twisted = str(TEAM / "new_scorer.py").replace("team_evals", "./team_evals")
    assert hook("Write", file_path=twisted, content="x").returncode == 2


def test_hook_is_inert_when_unlocked(unlocked):
    assert bash("rm connect4/team_evals/.locked").returncode == 0
    assert hook("Edit", file_path=str(TEAM / "gates.toml"),
                old_string="a", new_string="b").returncode == 0


def test_lock_c4_strips_write_bits_and_unlock_restores_them():
    originals = {p: stat.S_IMODE(p.stat().st_mode) for p in PROTECTED}
    existed, content = _save_lock()
    try:
        subprocess.run(["make", "lock-c4"], cwd=ROOT, check=True,
                      capture_output=True, text=True)
        assert LOCK.exists()
        for p in PROTECTED:
            assert stat.S_IMODE(p.stat().st_mode) & 0o222 == 0, p

        subprocess.run(["make", "unlock-c4"], cwd=ROOT, check=True,
                       capture_output=True, text=True)
        assert not LOCK.exists()
        for p in PROTECTED:
            assert stat.S_IMODE(p.stat().st_mode) == originals[p], p
    finally:
        # Restore modes and any real lock even if an assertion failed mid-way.
        for p, mode in originals.items():
            os.chmod(p, mode)
        _restore_lock(existed, content)
