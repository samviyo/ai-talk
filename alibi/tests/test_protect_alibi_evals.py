"""Lock-layer tests through the real entry points: the PreToolUse hook and make."""
from __future__ import annotations

import contextlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from alibi import evals

ROOT = Path(evals.__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "protect-alibi-evals.sh"
TEAM = ROOT / "alibi" / "team_evals"
LOCK = TEAM / ".locked"
PROTECTED = [TEAM / "gates.toml", *sorted(TEAM.glob("*.py"))]


def _save_lock() -> tuple[bool, bytes | None]:
    existed = LOCK.exists()
    return existed, LOCK.read_bytes() if existed else None


@contextlib.contextmanager
def _writable_team_dir():
    """make lock-alibi strips the dir's write bit; borrow it to add or remove the marker."""
    mode = TEAM.stat().st_mode
    TEAM.chmod(mode | stat.S_IWUSR)
    try:
        yield
    finally:
        TEAM.chmod(mode)


def _restore_lock(existed: bool, content: bytes | None) -> None:
    """Real locks go back; markers this test created are removed."""
    with _writable_team_dir():
        if existed:
            LOCK.unlink(missing_ok=True)
            LOCK.write_bytes(content or b"")
        else:
            LOCK.unlink(missing_ok=True)


@pytest.fixture
def locked():
    """Install .locked for the test; a real pre-existing lock is restored after."""
    existed, content = _save_lock()
    with _writable_team_dir():
        LOCK.write_bytes(b"")
    try:
        yield LOCK
    finally:
        _restore_lock(existed, content)


@pytest.fixture
def unlocked():
    """Remove .locked for the test; a real pre-existing lock is restored after."""
    existed, content = _save_lock()
    with _writable_team_dir():
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
    "python3 -c \"open('alibi/team_evals/gates.toml','w').write('')\"",
    "python3 -c \"import pathlib; pathlib.Path('alibi/./team_evals/x.py').write_text('x')\"",
    "uv run python -c \"open('alibi/team_evals/s.py','w')\"",
    "rm alibi/team_evals/.locked",
    "rm -f alibi/team_evals/workshop_scorers.py",
    "mv alibi/team_evals/gates.toml /tmp/x",
    "chmod +w alibi/team_evals/gates.toml",
    "echo x > alibi/team_evals/gates.toml",
    "cat alibi/team_evals/gates.toml > alibi/team_evals/copy.toml",
    "cd alibi/team_evals && touch evil.py",
    "ls alibi/team_evals | xargs rm -f",
    "make -s unlock-alibi",
    "make unlock-alibi",
    "make -C . unlock-alibi",
    # quoting and spelling dodges must not slip past the path check
    "rm -rf alibi/team_''evals",
    "rm -rf 'alibi/team_evals'",
    "rm -rf \"alibi/team_evals\"",
    "rm -rf alibi/team_evals/",
    "rm -rf alibi/team_evals/../team_evals",
    "rm -rf alibi/team_evals/*",
    "rm -rf alibi/team_*",
    # an ancestor of a protected path carries the whole tree away with it
    "rm -rf alibi",
    # a variable or cd can carry a protected path into a later segment
    "X=alibi/team_evals; rm -rf $X",
    "X=alibi; rm -rf $X/team_evals",
    "export D=alibi && rm -rf $D/datasets",
    "cd alibi && rm -rf team_evals",
    "rm -rf alibi",
    "rm -rf reference/alibi",
    "rm -rf .claude",
    "rm -rf .",
    "touch alibi",
    "mv alibi/datasets /tmp/x",
    # writes to the other protected locations
    "chmod u+w alibi/datasets/heldout.jsonl",
    "chmod u+w reference/alibi/organiser_evals.py",
    "chmod u+w .claude/hooks/protect-alibi-evals.sh",
    "rm .claude/settings.json",
    "mv reference/alibi/gates.toml /tmp/x",
    "echo x > reference/alibi/gates.toml",
    "echo x > alibi/datasets/dev.jsonl",
    "cat /etc/passwd | tee .claude/settings.json",
    "shlex-unbalanced ' rm -rf alibi/team_evals",
    # shell expansion and normalised redirect targets resolve to protected paths
    "A=ali; B=bi; mv \"$A$B\" /tmp/alibi-away",
    "mv .clau?e /tmp/no-hooks",
    "cat /dev/null > .claude/hooks/../settings.json",
    "cat /dev/null > reference/werewolf/../alibi/organiser_evals.py",
    # pytest options that write or delete paths
    "pytest --basetemp=alibi/team_evals",
    "uv run pytest -p some_plugin alibi",
    "pytest --junitxml .claude/settings.json alibi",
    "sed -n 'w .claude/settings.json' /dev/null",
    "sed -n -i.bak 's/.*/x/' .claude/settings.json",
    "cat /dev/null > \"$PWD\"/.clau?e/settings.json",
])
def test_writes_and_unlock_are_blocked_while_locked(command):
    r = bash(command)
    assert r.returncode == 2, f"{command}: {r.stderr}"


@pytest.mark.usefixtures("locked")
@pytest.mark.parametrize("command", [
    "cat alibi/team_evals/gates.toml",
    "less alibi/team_evals/gates.toml",
    "head alibi/team_evals/gates.toml",
    "tail alibi/team_evals/gates.toml",
    "grep min alibi/team_evals/gates.toml",
    "rg min alibi/team_evals/gates.toml",
    "ls alibi/team_evals",
    "wc -l alibi/team_evals/gates.toml",
    "diff alibi/team_evals/gates.toml alibi/team_evals/gates.toml",
    "git diff -- alibi/team_evals/gates.toml",
    "git log --oneline -- alibi/team_evals",
    "git status -- alibi/team_evals",
    "cat alibi/team_evals/gates.toml | grep min",
    "sed -n 1,20p alibi/team_evals/gates.toml",
    "sed -n '1,$p' alibi/team_evals/gates.toml",
    "grep -i min alibi/team_evals/gates.toml",
    "rg -i min alibi/team_evals/gates.toml",
    "git log -p -- alibi/team_evals",
    "ALIBI_HELDOUT_SEED=$seed make eval-alibi",
    "ALIBI_HELDOUT_SEED=7 uv run python -m alibi.evals --split heldout",
    "git show HEAD:alibi/team_evals/gates.toml",
    "python -m pytest alibi",
    "uv run pytest -q alibi",
    "make eval-alibi",
    "make check-alibi",
    "make eval-alibi ALIBI_BOT=alibi.bots.naive:audit ALIBI_TRIALS=2",
    "python -m alibi.evals --bot alibi.bots.naive:audit",
    "python -m alibi.checks",
    "uv run python -m alibi.evals --split dev --trials 1",
    "uv run pytest -q alibi",
    "uv run python -m alibi.evals --split dev",
    "ls alibi",
    "ls reference/alibi",
    "cat reference/alibi/gates.toml",
    "cat alibi/datasets/dev.jsonl",
    "git diff -- reference/alibi/organiser_evals.py",
    "diff alibi/datasets/dev.jsonl alibi/datasets/dev.jsonl",
])
def test_reads_and_eval_runs_pass_while_locked(command):
    r = bash(command)
    assert r.returncode == 0, f"{command}: {r.stderr}"


@pytest.mark.usefixtures("locked")
def test_unrelated_commands_pass_while_locked():
    assert bash("cat README.md").returncode == 0
    assert bash("rm -rf /tmp/foo").returncode == 0
    assert bash("rm -rf scratch").returncode == 0


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


@pytest.mark.usefixtures("locked")
def test_other_protected_paths_are_blocked_for_edits():
    cases = [
        ("Edit", {"file_path": str(ROOT / "reference" / "alibi" / "organiser_evals.py"),
                  "old_string": "a", "new_string": "b"}),
        ("Edit", {"file_path": str(ROOT / ".claude" / "hooks" / "protect-alibi-evals.sh"),
                  "old_string": "a", "new_string": "b"}),
        ("Edit", {"file_path": str(ROOT / ".claude" / "settings.json"),
                  "old_string": "a", "new_string": "b"}),
        ("Write", {"file_path": str(ROOT / "alibi" / "datasets" / "new.jsonl"),
                   "content": "x"}),
        ("Write", {"file_path": str(ROOT / "reference" / "alibi" / "evil.py"),
                   "content": "x"}),
        ("Write", {"file_path": str(ROOT / ".claude" / "settings.json"),
                   "content": "x"}),
        ("Edit", {"file_path": str(ROOT) + "/reference/alibi/..//alibi/organiser_evals.py",
                  "old_string": "a", "new_string": "b"}),
    ]
    for tool, tool_input in cases:
        path = tool_input["file_path"]
        r = hook(tool, **tool_input)
        assert r.returncode == 2, f"{path}: {r.stderr}"


@pytest.mark.usefixtures("locked")
def test_unrelated_edit_paths_pass_while_locked():
    ok = [
        ("Edit", {"file_path": str(ROOT / "alibi" / "evals.py"),
                  "old_string": "a", "new_string": "b"}),
        ("Edit", {"file_path": str(ROOT / "alibi" / "tests" / "test_evals.py"),
                  "old_string": "a", "new_string": "b"}),
        ("Write", {"file_path": str(ROOT / "connect4" / "notes.md"), "content": "x"}),
    ]
    for tool, tool_input in ok:
        assert hook(tool, **tool_input).returncode == 0, tool_input["file_path"]


def test_hook_is_inert_when_unlocked(unlocked):
    assert bash("rm alibi/team_evals/.locked").returncode == 0
    assert bash("rm -rf alibi/team_evals").returncode == 0
    assert bash("chmod u+w alibi/datasets/heldout.jsonl").returncode == 0
    assert bash("make unlock-alibi").returncode == 0
    assert hook("Edit", file_path=str(TEAM / "gates.toml"),
                old_string="a", new_string="b").returncode == 0
    assert hook("Edit", file_path=str(ROOT / "reference" / "alibi" / "organiser_evals.py"),
                old_string="a", new_string="b").returncode == 0
    assert hook("Edit", file_path=str(ROOT / ".claude" / "settings.json"),
                old_string="a", new_string="b").returncode == 0


def test_lock_alibi_strips_write_bits_and_unlock_restores_them():
    tracked = PROTECTED + [TEAM, ROOT / "alibi" / "datasets"]
    originals = {p: stat.S_IMODE(p.stat().st_mode) for p in tracked}
    existed, content = _save_lock()
    try:
        subprocess.run(["make", "lock-alibi"], cwd=ROOT, check=True,
                       capture_output=True, text=True)
        assert LOCK.exists()
        for p in PROTECTED:
            assert stat.S_IMODE(p.stat().st_mode) & 0o222 == 0, p
        for d in (TEAM, ROOT / "alibi" / "datasets"):
            assert stat.S_IMODE(d.stat().st_mode) & 0o222 == 0, d

        subprocess.run(["make", "unlock-alibi"], cwd=ROOT, check=True,
                       capture_output=True, text=True)
        assert not LOCK.exists()
        for p in PROTECTED:
            assert stat.S_IMODE(p.stat().st_mode) == originals[p], p
        for d in (TEAM, ROOT / "alibi" / "datasets"):
            assert stat.S_IMODE(d.stat().st_mode) & stat.S_IWUSR, d
    finally:
        # Restore modes and any real lock even if an assertion failed mid-way.
        for p, mode in originals.items():
            os.chmod(p, mode)
        _restore_lock(existed, content)


@pytest.mark.usefixtures("locked")
@pytest.mark.parametrize("path", [
    "alibi/golden/checks/run.json",
    "alibi/golden/.locked",
    "alibi/migrate/__main__.py",
    "alibi/__init__.py",
    "alibi/checks.py",
    "alibi/scorers.py",
])
def test_migration_truth_is_blocked_for_edits(path):
    r = hook("Edit", file_path=str(ROOT / path), old_string="a", new_string="b")
    assert r.returncode == 2, r.stderr


def test_golden_lock_alone_arms_the_hook(unlocked):
    golden = ROOT / "alibi" / "golden"
    if golden.exists():
        pytest.skip("a real alibi/golden exists")
    golden.mkdir()
    try:
        (golden / ".locked").touch()
        assert hook("Edit", file_path=str(ROOT / "alibi/checks.py"),
                    old_string="a", new_string="b").returncode == 2
    finally:
        (golden / ".locked").unlink()
        golden.rmdir()


@pytest.mark.usefixtures("locked")
def test_port_sources_stay_editable():
    assert hook("Edit", file_path=str(ROOT / "alibi/zig/src/score.zig"),
                old_string="a", new_string="b").returncode == 0


@pytest.mark.usefixtures("locked")
@pytest.mark.parametrize("cmd,code", [
    ("alibi/zig/zig-out/bin/alibi-check run alibi/golden/checks/rows.jsonl "
     "alibi/golden/checks/gates.toml", 0),
    ("./alibi/zig/zig-out/bin/alibi-check score alibi/golden/practice/parse.json", 0),
    ("make parity", 0),
    ("make parity-score && make zig-build", 0),
    ("alibi/zig/zig-out/bin/alibi-check run x y > alibi/golden/checks/run.json", 2),
    ("cp out.json alibi/golden/checks/run.json", 2),
    ("make unlock-golden", 2),
    ("chmod -R u+w alibi/golden", 2),
])
def test_migration_commands(cmd, code):
    assert bash(cmd).returncode == code
