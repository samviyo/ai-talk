"""OpenAI-compatible LLM proxy to Bifrost, with per-seat budget metering."""

from __future__ import annotations

import json
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

MAX_OUTPUT_TOKENS = 2048
UPSTREAM_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class Prices:
    # per-1M-token USD
    input: dict[str, float]
    output: dict[str, float]

    @classmethod
    def load(cls, path: Path) -> "Prices":
        raw = tomllib.loads(path.read_text())
        models = raw.get("models", {})
        return cls(
            input={m: float(v["input"]) for m, v in models.items()},
            output={m: float(v["output"]) for m, v in models.items()},
        )

    def allowed(self, model: str) -> bool:
        return model in self.input

    def cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self.input[model] + completion_tokens * self.output[model]
        ) / 1_000_000


class LLMProxy:
    def __init__(
        self,
        prices: Prices,
        base_url: str | None,
        fake: bool,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.prices = prices
        self.base_url = (base_url or "").rstrip("/")
        self.fake = fake
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=UPSTREAM_TIMEOUT_S
        )

    async def chat_completions(
        self,
        payload: dict[str, Any],
        api_key: str,
        legal_targets: list[int],
    ) -> tuple[int, dict[str, Any], dict[str, float]]:
        """Returns (status, body, charges); charges.usd is what to bill."""
        err = self._validate(payload)
        if err is not None:
            return err[0], {"error": {"code": err[1], "message": err[2]}}, {}
        model = payload["model"]
        max_tokens = int(payload["max_tokens"])
        input_est = max(1, len(json.dumps(payload.get("messages") or [])) // 4)
        if self.fake:
            body, usage = self._fake_response(model, legal_targets, max_tokens)
            usd = self.prices.cost(model, usage["prompt_tokens"], usage["completion_tokens"])
            return 200, body, {"usd": usd}
        headers = {"Authorization": f"Bearer {api_key}", "x-bf-vk": api_key}
        try:
            async with self._client() as client:
                resp = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json={**payload, "stream": False},
                    headers=headers,
                )
            body = resp.json()
        except Exception:
            # upstream error/timeout: charge the input estimate only
            return 502, {"error": {"code": "upstream_error"}}, {"usd": self.prices.cost(model, input_est, 0)}
        if resp.status_code != 200:
            return resp.status_code, body, {"usd": self.prices.cost(model, input_est, 0)}
        usage = body.get("usage") or {}
        if usage.get("prompt_tokens") is not None and usage.get("completion_tokens") is not None:
            usd = self.prices.cost(model, usage["prompt_tokens"], usage["completion_tokens"])
        else:
            usd = self.prices.cost(model, input_est, max_tokens)
        return 200, body, {"usd": usd}

    def _validate(self, payload: dict[str, Any]) -> tuple[int, str, str] | None:
        model = payload.get("model")
        if not isinstance(model, str) or not self.prices.allowed(model):
            return 400, "model_not_allowed", f"unknown model {model!r}"
        if payload.get("stream"):
            return 400, "stream_not_allowed", "stream=true is rejected"
        mt = payload.get("max_tokens")
        if mt is None:
            return 400, "max_tokens_required", "max_tokens is required"
        if not isinstance(mt, int) or not 1 <= mt <= MAX_OUTPUT_TOKENS:
            return 400, "max_tokens_too_large", f"max_tokens must be <= {MAX_OUTPUT_TOKENS}"
        return None

    @staticmethod
    def _fake_response(
        model: str, legal_targets: list[int], max_tokens: int
    ) -> tuple[dict[str, Any], dict[str, int]]:
        target = legal_targets[0] if legal_targets else 0
        content = json.dumps({"move": target})
        usage = {"prompt_tokens": 64, "completion_tokens": min(16, max_tokens)}
        body = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                **usage,
                "total_tokens": usage["prompt_tokens"] + usage["completion_tokens"],
            },
        }
        return body, usage
