"""Team scorer discovery and gates: load `team_evals/*.py`, apply `gates.toml`.

A *scorer* is any `score_*(input, output, expected=None, **kw) -> float|bool|None`
in `connect4/team_evals/*.py`. It runs over every dataset; returning None means
"not applicable". A *gate* (`[score_x] min=0.95`) is a threshold on the scorer
mean; a scorer with no gate is reported as an info row.
"""

from __future__ import annotations

import functools
import importlib
import pkgutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ScorerFn = Callable[..., float | bool | None]

TEAM_EVALS_DIR = Path(__file__).resolve().parent / "team_evals"


@dataclass(frozen=True)
class Scorer:
    name: str
    fn: ScorerFn


@dataclass(frozen=True)
class Gate:
    """A gate: the scorer mean must be at least `min` over applicable rows."""
    min: float


def load_scorers() -> list[Scorer]:
    """Import every `team_evals` submodule and collect `score_*` functions."""
    scorers: list[Scorer] = []
    if not TEAM_EVALS_DIR.exists():
        return scorers
    for mod_info in sorted(pkgutil.iter_modules([str(TEAM_EVALS_DIR)]), key=lambda m: m.name):
        module = importlib.import_module(f"connect4.team_evals.{mod_info.name}")
        for attr in sorted(vars(module)):
            if attr.startswith("score_") and callable(getattr(module, attr)):
                scorers.append(Scorer(name=attr, fn=getattr(module, attr)))
    return scorers


def load_gates() -> dict[str, Gate]:
    path = TEAM_EVALS_DIR / "gates.toml"
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
