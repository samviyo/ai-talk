"""Prompt-only LLM bot via an OpenAI-compatible endpoint (Bifrost).

Env: BIFROST_BASE_URL, BIFROST_API_KEY, C4_MODEL. One call per move, no
tools, no legal-move list — the workshop is about teams fixing that. An
unparsable or illegal reply is returned raw so the eval counts it illegal.
"""

from __future__ import annotations

import os
import re
import time

from openai import OpenAI

from ..engine import Board
from .base import MoveResult

SYSTEM = "You're playing Connect Four. You win by getting four of your pieces in a row."
USER_TEMPLATE = """Here's the board. You are {piece}. Columns are numbered 0-6, left to right.

{board}

Reply with the column number of your move."""

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ["BIFROST_BASE_URL"].rstrip("/") + "/v1",  # gateway-env exports the bare origin
            api_key=os.environ["BIFROST_API_KEY"],
        )
    return _client


def _parse_column(text: str):
    m = re.search(r"\b([0-9])\b", text)
    return int(m.group(1)) if m else text.strip()


def choose(board: Board, player: int) -> MoveResult:
    prompt = USER_TEMPLATE.format(piece="X" if player == 1 else "O", board=board.render())
    start = time.perf_counter()
    resp = _get_client().chat.completions.create(
        model=os.environ.get("C4_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
    )
    latency = time.perf_counter() - start
    text = resp.choices[0].message.content or ""
    usage = resp.usage
    return MoveResult(
        column=_parse_column(text),
        latency_s=latency,
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )
