"""House bots for Werewolf practice tables. Deterministic, no LLM calls."""

from .brains import BRAINS, make_brain

__all__ = ["BRAINS", "make_brain"]
