"""check-alibi -> Zig migration: pinned toolchain, frozen golden outputs, parity checks.

    python -m alibi.migrate zig                    # install the pinned Zig
    python -m alibi.migrate build                  # zig build the port
    python -m alibi.migrate golden                 # freeze the Python tool's outputs (then locked)
    python -m alibi.migrate parity [--stage S]     # port vs golden, end to end or one stage
    python -m alibi.migrate reveal --seed N        # port vs Python on fresh held-out cases
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .. import checks
from ..scorers import load_scorers, team_evals_dir
from . import cases, reference, zig

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"

ROOT = Path(__file__).resolve().parents[2]
PORT_DIR = ROOT / "alibi" / "zig"
DEFAULT_BIN = PORT_DIR / "zig-out" / "bin" / "alibi-check"
GOLDEN_DIR = ROOT / "alibi" / "golden"
STAGES = ("parse", "score", "report", "run")
TOLERANCE = 1e-9
MAX_DIFFS = 8


# -- comparing ---------------------------------------------------------------

def _canon_failures(fs: Any) -> Any:
    if not isinstance(fs, list):
        return fs
    return sorted(fs, key=lambda f: json.dumps(f, sort_keys=True))


def diff(want: Any, got: Any, path: str = "$") -> list[str]:
    """Differences between canonical outputs; numbers within TOLERANCE, failures unordered."""
    if isinstance(want, bool) or isinstance(got, bool):
        return [] if want is got else [f"{path}: want {json.dumps(want)}, got {json.dumps(got)}"]
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        return [] if want == got or abs(want - got) <= TOLERANCE else [f"{path}: want {want!r}, got {got!r}"]
    if isinstance(want, dict) and isinstance(got, dict):
        out = []
        for k in want:
            if k not in got:
                out.append(f"{path}.{k}: missing")
            else:
                w, g = want[k], got[k]
                if k == "failures":
                    w, g = _canon_failures(w), _canon_failures(g)
                out += diff(w, g, f"{path}.{k}")
        out += [f"{path}.{k}: unexpected key" for k in got if k not in want]
        return out
    if isinstance(want, list) and isinstance(got, list):
        if len(want) != len(got):
            return [f"{path}: want {len(want)} items, got {len(got)}"]
        return [d for i, (w, g) in enumerate(zip(want, got)) for d in diff(w, g, f"{path}[{i}]")]
    if want != got:
        return [f"{path}: want {json.dumps(want)[:80]}, got {json.dumps(got)[:80]}"]
    return []


# -- running the port --------------------------------------------------------

def run_port(binary: Path, args: list[str]) -> tuple[Any, str | None]:
    """Run one port command; returns (parsed JSON stdout, error text)."""
    try:
        proc = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return None, f"no port binary at {binary}: run make zig-build"
    except subprocess.TimeoutExpired:
        return None, "timed out after 60s"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-6:]
        return None, f"exit {proc.returncode}: " + " | ".join(tail)
    try:
        return json.loads(proc.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"stdout is not JSON ({exc}): {proc.stdout[:120]!r}"


def stage_args(stage: str, case_dir: Path) -> list[str]:
    """Port arguments for one stage; per-stage checks feed golden upstream outputs."""
    rows, gates = str(case_dir / "rows.jsonl"), str(case_dir / "gates.toml")
    return {
        "parse": ["parse", rows, gates],
        "score": ["score", str(case_dir / "parse.json")],
        "report": ["report", str(case_dir / "parse.json"), str(case_dir / "score.json")],
        "run": ["run", rows, gates],
    }[stage]


def check_case(binary: Path, case_dir: Path, stage: str, want: Any) -> bool:
    got, err = run_port(binary, stage_args(stage, case_dir))
    diffs = [err] if err else diff(want, got)
    ok = not diffs
    color, verdict = (GREEN, "GREEN") if ok else (RED, "RED  ")
    print(f"  {color}{verdict}{RESET} {case_dir.name:<10} {stage}")
    for d in diffs[:MAX_DIFFS]:
        print(f"       {d}")
    if len(diffs) > MAX_DIFFS:
        print(f"       ... {len(diffs) - MAX_DIFFS} more")
    return ok


def build() -> int:
    exe = zig.install()
    proc = subprocess.run([str(exe), "build", "-Doptimize=ReleaseSafe"], cwd=PORT_DIR)
    return proc.returncode


def _verdict(ok: bool) -> int:
    print(f"\n{GREEN}GREEN{RESET}\n" if ok else f"\n{RED}RED{RESET}\n")
    return 0 if ok else 1


# -- golden ------------------------------------------------------------------

def _write_case(case_dir: Path, rows_text: str, evals_dir: Path) -> None:
    case_dir.mkdir(parents=True)
    (case_dir / "rows.jsonl").write_text(rows_text, encoding="utf-8")
    shutil.copyfile(evals_dir / "gates.toml", case_dir / "gates.toml")
    out = reference.stages(case_dir / "rows.jsonl", case_dir, load_scorers(str(evals_dir)))
    for stage, payload in out.items():
        (case_dir / f"{stage}.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")


def _lock(directory: Path) -> None:
    (directory / ".locked").touch()
    for path in sorted(directory.rglob("*"), reverse=True):
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    directory.chmod(stat.S_IMODE(directory.stat().st_mode) & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def golden(out: Path, evals_dir: Path, *, require_lock: bool) -> int:
    if (out / ".locked").exists():
        print(f"{out} is locked: golden outputs are frozen once made (a human runs make unlock-golden)")
        return 1
    if require_lock and not (evals_dir / ".locked").exists():
        print("lock your scorers first (make lock-alibi): golden outputs freeze what they decide")
        return 1
    if not load_scorers(str(evals_dir)):
        print(f"no score_* functions in {evals_dir}")
        return 1
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    _write_case(out / "checks", checks.CHECKS_PATH.read_text(encoding="utf-8"), evals_dir)
    practice = cases.generate(cases.PRACTICE_SEED, split="dev")
    _write_case(out / "practice", cases.dump(practice), evals_dir)
    for case in ("checks", "practice"):
        report = json.loads((out / case / "report.json").read_text())
        print(f"  {case:<10} {len(report['rows'])} rows, Python verdict "
              f"{'GREEN' if report['green'] else 'RED'}")
    if require_lock:
        _lock(out)
        print(f"froze and locked {out}")
    return 0


# -- parity and reveal ---------------------------------------------------------

def parity(binary: Path, golden_dir: Path, stages: list[str]) -> int:
    case_dirs = sorted(p for p in golden_dir.glob("*") if (p / "run.json").exists())
    if not case_dirs:
        print(f"no golden outputs in {golden_dir}: run make golden first")
        return 1
    print(f"\n== parity: {binary.name} vs golden ({', '.join(stages)}) ==")
    ok = True
    for case_dir in case_dirs:
        for stage in stages:
            want = json.loads((case_dir / f"{stage}.json").read_text())
            ok = check_case(binary, case_dir, stage, want) and ok
    return _verdict(ok)


def reveal(binary: Path, seed: int, evals_dir: Path) -> int:
    print(f"\n== reveal: {binary.name} vs Python on held-out seed {seed} ==")
    with tempfile.TemporaryDirectory() as truth, tempfile.TemporaryDirectory() as tmp:
        _write_case(Path(truth) / "heldout", cases.dump(cases.generate(seed)), evals_dir)
        want = json.loads((Path(truth) / "heldout" / "run.json").read_text())
        # The port sees only the inputs, never the expected outputs beside them.
        case_dir = Path(tmp) / "heldout"
        case_dir.mkdir()
        for name in ("rows.jsonl", "gates.toml"):
            shutil.copyfile(Path(truth) / "heldout" / name, case_dir / name)
        print(f"  {len(want['rows'])} fresh rows; Python verdict "
              f"{'GREEN' if want['green'] else 'RED'}")
        return _verdict(check_case(binary, case_dir, "run", want))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="alibi.migrate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("zig")
    sub.add_parser("zig-bundle")
    sub.add_parser("build")
    g = sub.add_parser("golden")
    g.add_argument("--team-evals-dir", default=None)
    g.add_argument("--out", default=None, help="scratch golden dir; skips the lock (organiser use)")
    p = sub.add_parser("parity")
    p.add_argument("--bin", default=None)
    p.add_argument("--stage", choices=STAGES, default="run")
    p.add_argument("--golden", default=str(GOLDEN_DIR))
    r = sub.add_parser("reveal")
    r.add_argument("--bin", default=None)
    r.add_argument("--seed", type=int, default=None)
    r.add_argument("--team-evals-dir", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "zig":
        print(zig.install())
        return 0
    if args.cmd == "zig-bundle":
        print(zig.bundle())
        return 0
    if args.cmd == "build":
        return build()
    if args.cmd == "golden":
        evals_dir = team_evals_dir(args.team_evals_dir)
        out = Path(args.out).resolve() if args.out else GOLDEN_DIR
        return golden(out, evals_dir, require_lock=args.out is None)

    binary = Path(args.bin).resolve() if args.bin else DEFAULT_BIN
    if args.bin is None and build() != 0:
        print(f"\n{RED}RED{RESET}  zig build failed\n")
        return 1
    if args.cmd == "parity":
        return parity(binary, Path(args.golden), [args.stage])
    seed = args.seed if args.seed is not None else os.environ.get("ALIBI_HELDOUT_SEED")
    if seed is None or not str(seed).lstrip("-").isdigit():
        print("ALIBI_HELDOUT_SEED must be a whole number: the organiser announces it at the reveal")
        return 1
    return reveal(binary, int(seed), team_evals_dir(args.team_evals_dir))


if __name__ == "__main__":
    sys.exit(main())
