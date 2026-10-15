"""decide(prompt) -> str for the deduction-puzzle and moderator-spoof runners.

Standalone (no game server): answers a self-contained deduction prompt with the
decide model. Used by `python -m evals.puzzles --module bot.puzzle_decide`.
"""
from __future__ import annotations

import os
from pathlib import Path

from .agent import LLMClient, load_config

_client: LLMClient | None = None


def _get() -> LLMClient:
    global _client
    if _client is None:
        server = os.environ.get("WW_SERVER", "")
        token = os.environ.get("WW_TOKEN", "")
        if not server or not token:
            raise SystemExit("puzzle_decide needs WW_SERVER and WW_TOKEN (LLM proxy)")
        _client = LLMClient(server.rstrip("/"), token, load_config())
    return _client


def decide(prompt: str) -> str:
    return _get().complete(_get()._config.decide_model, prompt)
