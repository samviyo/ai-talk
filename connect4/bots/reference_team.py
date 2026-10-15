"""Team bot: LLM decides every move; code only supplies board information.

House rule: the model picks the column. Code may compute facts (legal moves,
immediate wins, squares that lose on the spot) and retry on an illegal or
unparsable reply, but never overrides a legal choice.
"""

from __future__ import annotations

import os
import re
import time

from openai import OpenAI

from ..engine import Board
from ..solver import blocking_moves, winning_moves
from .base import MoveResult

SYSTEM = (
    "You're playing Connect Four. You win by getting four of your pieces in a "
    "row. Reply with only the column number of your move — a single digit."
)
USER_TEMPLATE = """Here's the board. You are {piece}. Columns are numbered 0-6, left to right.

{board}

Legal columns: {legal}
{tactical}
Which column do you play?"""

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=os.environ["BIFROST_BASE_URL"],
            api_key=os.environ["BIFROST_API_KEY"],
        )
    return _client


def _parse_column(text: str):
    """Take the last standalone digit: models explain first, then answer."""
    digits = re.findall(r"\b([0-6])\b", text)
    return int(digits[-1]) if digits else None


def _tactical_notes(board: Board, player: int) -> str:
    wins = sorted(winning_moves(board, player))
    if wins:
        return f"You can win immediately by playing column {wins[0]}."
    safe = sorted(blocking_moves(board, player))
    losing = [c for c in board.legal_moves() if c not in safe]
    if losing:
        return (
            f"Playing columns {losing} lets the opponent win immediately next "
            f"turn; safe columns are {safe}."
        )
    return "Neither side has an immediate winning move."


def _ask(prompt: str):
    return _get_client().chat.completions.create(
        model=os.environ.get("C4_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=4096,  # reasoning models burn ~900 hidden tokens before answering
    )


def choose(board: Board, player: int) -> MoveResult:
    prompt = USER_TEMPLATE.format(
        piece="X" if player == 1 else "O",
        board=board.render(),
        legal=board.legal_moves(),
        tactical=_tactical_notes(board, player),
    )
    legal = board.legal_moves()
    latency = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    column = None
    # Retry on an illegal/unparsable reply; the model still picks the move.
    for _ in range(2):
        try:
            start = time.perf_counter()
            resp = _ask(prompt)
            latency += time.perf_counter() - start
        except Exception:
            continue
        text = resp.choices[0].message.content or ""
        if resp.usage:
            prompt_tokens += resp.usage.prompt_tokens
            completion_tokens += resp.usage.completion_tokens
        column = _parse_column(text)
        if isinstance(column, int) and column in legal:
            return MoveResult(
                column=column,
                latency_s=latency,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
    return MoveResult(
        column=column,
        latency_s=latency,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
