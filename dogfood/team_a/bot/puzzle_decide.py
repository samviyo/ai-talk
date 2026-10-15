"""decide(prompt) entry point for evals.puzzles and evals.spoof.

Routes a free-text deduction prompt through the same LLM proxy + system guard the
live bot uses, so the puzzle scoreboard measures the same brain the game does.
Needs WW_SERVER and WW_TOKEN pointing at any running practice server.
"""
from __future__ import annotations

import os

from .agent import LLMClient, load_config


def decide(prompt: str) -> str:
    config = load_config()
    server = os.environ.get("WW_SERVER", "http://127.0.0.1:8000")
    token = os.environ.get("WW_TOKEN", "")
    client = LLMClient(server, token, config)
    return client.complete(config.decide_model, prompt)
