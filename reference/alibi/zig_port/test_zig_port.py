"""Builds the Zig stubs and the reference port with the pinned Zig; skipped until `make zig`."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from alibi.migrate import __main__ as migrate
from alibi.migrate import zig

ROOT = Path(__file__).resolve().parents[3]
EVALS = ROOT / "reference" / "alibi"
PORT = Path(__file__).resolve().parent

pytestmark = pytest.mark.skipif(not zig.ZIG.exists(), reason="pinned Zig not installed: make zig")


def _build(directory: Path) -> Path:
    subprocess.run([str(zig.ZIG), "build"], cwd=directory, check=True, capture_output=True)
    return directory / "zig-out" / "bin" / "alibi-check"


def test_team_stubs_compile_and_fail_loudly():
    binary = _build(ROOT / "alibi" / "zig")
    rows = str(ROOT / "alibi" / "datasets" / "scorer_checks.jsonl")
    proc = subprocess.run([str(binary), "run", rows, str(EVALS / "gates.toml")],
                          capture_output=True, text=True)
    assert proc.returncode != 0 and "NotImplemented" in proc.stderr


def test_reference_port_matches_python(tmp_path, capsys):
    binary = _build(PORT)
    golden = tmp_path / "golden"
    assert migrate.golden(golden, EVALS, require_lock=False) == 0
    assert migrate.parity(binary, golden, list(migrate.STAGES)) == 0, capsys.readouterr().out
    assert migrate.reveal(binary, 913, EVALS) == 0, capsys.readouterr().out
