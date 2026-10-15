"""Shared bot plumbing: move result wrapper and bot loading."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Callable

from ..engine import Board

Choose = Callable[[Board, int], Any]


@dataclass
class MoveResult:
    column: Any  # intentionally untyped: LLM bots may return garbage
    latency_s: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    extra: dict = field(default_factory=dict)


def unwrap(result: Any) -> tuple[Any, MoveResult]:
    if isinstance(result, MoveResult):
        return result.column, result
    return result, MoveResult(column=result)


def load_bot(spec: str) -> Choose:
    """Load 'module:attr' or bare 'module' (defaults to attr 'choose')."""
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr or "choose")
