"""`make setup` smoke test: one tiny chat call through the same client and model `make check` uses."""
from __future__ import annotations

import sys

from .agent import load_config
from .puzzle_decide import _client


def main() -> int:
    model = load_config().decide_model
    try:
        resp = _client.chat.completions.create(
            model=model, max_tokens=5, messages=[{"role": "user", "content": "Reply with OK."}]
        )
    except Exception as e:  # noqa: BLE001 - any failure here means setup is not done
        print(f"bifrost: chat call with {model} failed: {type(e).__name__}: {e}")
        return 1
    if not resp.choices:
        print(f"bifrost: {model} returned no choices")
        return 1
    print(f"bifrost: chat call with {model} works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
