"""Team scorer discovery and gates for `alibi/team_evals/*.py`.

A *scorer* is any `score_*(input, output, expected=None, **kw) -> float|bool|None`.
A *gate* (`[score_x] min=0.8`) is a threshold on the scorer mean. The loader dir
is overridable (env `ALIBI_TEAM_EVALS_DIR`) so the reference suite runs through
the same harness.
"""

from __future__ import annotations

import functools
import os
import pkgutil
import tomllib
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ScorerFn = Callable[..., float | bool | None]

TEAM_EVALS_DIR = Path(__file__).resolve().parent / "team_evals"


def team_evals_dir(override: str | None = None) -> Path:
    dir_path = override or os.environ.get("ALIBI_TEAM_EVALS_DIR")
    if dir_path:
        return Path(dir_path).resolve()
    return TEAM_EVALS_DIR


@dataclass(frozen=True)
class Scorer:
    name: str
    fn: ScorerFn


@dataclass(frozen=True)
class Gate:
    """A gate: the scorer mean must be at least `min` over applicable rows."""

    min: float


def load_scorers(override_dir: str | None = None) -> list[Scorer]:
    """Import every `team_evals` module in the dir and collect `score_*` functions."""
    scorers: list[Scorer] = []
    directory = team_evals_dir(override_dir)
    if not directory.exists():
        return scorers
    for mod_info in sorted(pkgutil.iter_modules([str(directory)]), key=lambda m: m.name):
        # Compile from source: timestamp-pyc validation misses same-second, same-size rewrites.
        source = (directory / f"{mod_info.name}.py").read_text()
        module = types.ModuleType(f"alibi_team_scorers_{mod_info.name}")
        exec(compile(source, str(directory / f"{mod_info.name}.py"), "exec"),
             module.__dict__)
        for attr in sorted(vars(module)):
            if attr.startswith("score_") and callable(getattr(module, attr)):
                scorers.append(Scorer(name=attr, fn=getattr(module, attr)))
    return scorers


def load_gates(override_dir: str | None = None) -> dict[str, Gate]:
    path = team_evals_dir(override_dir) / "gates.toml"
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text())
    return {name: Gate(min=float(v["min"])) for name, v in raw.items()
            if isinstance(v, dict) and "min" in v}


def wrapped(fn: ScorerFn) -> ScorerFn:
    """Wrap a team scorer: raises become NaN; the first error text is kept."""
    @functools.wraps(fn)
    def call(input: Any, output: Any, expected: Any = None, **kw) -> float | None:
        try:
            value = fn(input=input, output=output, expected=expected)
        except Exception as exc:
            # First raise wins; later ones only add to the error count.
            call.first_error = getattr(call, "first_error", None) or f"{type(exc).__name__}: {exc}"
            return float("nan")
        if value is None:
            return None
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        return float(value)
    return call
