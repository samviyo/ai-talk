"""Offline decide hook for `make check`: your bot's prompt and model, no game server.

The puzzle and spoof harnesses call decide(prompt) -> reply text. Point this at the
same system prompt and model your bot uses in a game, so the score reflects your bot.
"""
from __future__ import annotations

import os

from openai import OpenAI

from .agent import _SYSTEM, load_config, strip_artifacts

_config = load_config()
_client = OpenAI(
    base_url=os.environ.get("BIFROST_BASE_URL", "").rstrip("/") + "/v1",
    api_key=os.environ.get("BIFROST_API_KEY", ""),
    default_headers={"x-bf-vk": os.environ.get("BIFROST_API_KEY", "")},  # Bifrost dev ignores Bearer and only checks this header
    max_retries=0,
)


def decide(prompt: str) -> str:
    resp = _client.chat.completions.create(
        model=_config.decide_model,
        max_tokens=_config.max_tokens,
        temperature=0,  # stable score across reruns
        messages=[{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
    )
    return strip_artifacts((resp.choices[0].message.content or "").strip())
