"""Shared server state: tables, tokens, prices, team keys."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from werewolf.engine import Config

from .llm import LLMProxy, Prices
from .table import Table

DEFAULT_PRICES = {
    "claude-haiku-5-5": {"input": 0.1, "output": 0.5},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0},
}

# Simulated cost per accepted action for a house-bot seat, by brain.
DEFAULT_HOUSE_PRICES = {
    "random": 0.002,
    "quiet": 0.004,
    "accuser": 0.012,
    "baseline": 0.008,
}


class AppState:
    def __init__(
        self,
        config: Config,
        n_tables: int,
        budget_usd: float,
        window_s: float,
        runs_dir: Path,
        prices: Prices,
        fake_llm: bool,
        base_url: str | None,
        teams_toml: dict[str, str] | None,  # team -> env var name
        default_key_env: str = "BIFROST_API_KEY",
        upstream_transport: Any = None,
        auto_restart: bool = True,
        min_window_s: float = 0.0,
        transition_s: float = 0.0,
        house_prices: dict[str, float] | None = None,
        powerup_prices: dict[str, float] | None = None,
        reveal_s: float = 1.5,
        beat_s: float = 2.5,
        manual_start: bool = False,
    ) -> None:
        self.config = config
        self.manual_start = manual_start
        self.budget_usd = budget_usd
        self.window_s = window_s
        self.runs_dir = runs_dir
        self.operator_token = secrets.token_urlsafe(16)
        self.proxy = LLMProxy(prices, base_url, fake_llm, transport=upstream_transport)
        self.tables: list[Table] = []
        self.token_index: dict[str, tuple[Table, int]] = {}
        self.teams_toml = teams_toml or {}
        self.default_key_env = default_key_env
        self.house_prices = house_prices or dict(DEFAULT_HOUSE_PRICES)
        from werewolf.engine import POWERUP_PRICES

        self.powerup_prices = dict(POWERUP_PRICES)
        if powerup_prices:
            self.powerup_prices.update({k: float(v) for k, v in powerup_prices.items()})
        for t in range(n_tables):
            name = f"table{t + 1}"
            tokens = [
                f"{name}-seat{s}-{secrets.token_urlsafe(8)}"
                for s in range(config.seats)
            ]
            if self.teams_toml:
                team_names = sorted(self.teams_toml)
                teams = [team_names[s % len(team_names)] for s in range(config.seats)]
            else:
                teams = [f"team{s}" for s in range(config.seats)]
            table = Table(
                name=name,
                config=config,
                budget_usd=budget_usd,
                window_s=window_s,
                runs_dir=runs_dir,
                tokens=tokens,
                teams=teams,
                auto_restart=auto_restart,
                min_window_s=min_window_s,
                transition_s=transition_s,
                reveal_s=reveal_s,
                beat_s=beat_s,
                manual_start=manual_start,
            )
            self.tables.append(table)
            for s, tok in enumerate(tokens):
                self.token_index[tok] = (table, s)

    def lookup(self, token: str | None) -> tuple[Table, int] | None:
        if not token:
            return None
        return self.token_index.get(token)

    def key_for(self, table: Table, seat: int) -> str | None:
        """Team virtual key, or the practice default key."""
        import os

        if self.teams_toml:
            env_name = self.teams_toml.get(table.seats[seat].team)
            if env_name:
                return os.environ.get(env_name)
        return os.environ.get(self.default_key_env)
