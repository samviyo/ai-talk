"""Fixtures: a real uvicorn server on a free port, fake LLM, short clock."""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from werewolf.engine import Config
from werewolf.server.app import create_app
from werewolf.server.llm import Prices
from werewolf.server.state import AppState

PRICES = Prices(
    input={"fake-model": 1.0, "claude-haiku-5-5": 0.1},
    output={"fake-model": 2.0, "claude-haiku-5-5": 0.5},
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunningServer:
    def __init__(self, state: AppState, server: uvicorn.Server, task: asyncio.Task):
        self.state = state
        self.server = server
        self.task = task
        self.port = server.config.port
        self.url = f"http://127.0.0.1:{self.port}"

    def token(self, seat: int, table: int = 0) -> str:
        return self.state.tables[table].seats[seat].token

    async def stop(self) -> None:
        self.server.should_exit = True
        await asyncio.wait_for(self.task, timeout=5)


async def start_server(
    tmp_path: Path,
    *,
    seed: int = 1,
    budget: float = 0.50,
    window_s: float = 1.0,
    fake_llm: bool = True,
    seats: int = 7,
    auto_restart: bool = False,
    transport: Any = None,
    min_window_s: float = 0.0,
    transition_s: float = 0.0,
    transition_on_start: bool = True,
    discussion_s: float = 1.0,
) -> RunningServer:
    config = Config(
        seats=seats, wolves=2, max_days=5, seed=seed,
        discussion_s=discussion_s, max_posts=3,
    )
    state = AppState(
        config=config,
        n_tables=1,
        budget_usd=budget,
        window_s=window_s,
        runs_dir=tmp_path / "runs",
        min_window_s=min_window_s,
        transition_s=transition_s,
        prices=PRICES,
        fake_llm=fake_llm,
        base_url="http://fake-upstream",
        teams_toml=None,
        upstream_transport=transport,
        auto_restart=auto_restart,
    )
    state.tables[0].transition_on_start = transition_on_start
    if not transition_on_start:
        state.tables[0]._transition_until = 0.0
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(state), host="127.0.0.1", port=port, log_level="error")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.05)
    return RunningServer(state, server, task)


async def mcp_call(
    url: str, token: str, tool: str, args: dict[str, Any]
) -> dict[str, Any]:
    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    async with streamable_http_client(url + "/mcp", http_client=http_client) as (r, w, *_):
        async with ClientSession(r, w) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
            structured = getattr(result, "structured_content", None)
            if structured is not None:
                return dict(structured)
            return json.loads(result.content[0].text)


async def wait_turn(url: str, token: str, timeout: float = 10.0) -> dict[str, Any]:
    """wait_for_turn until an obligation (or over) appears."""
    while True:
        turn = await mcp_call(url, token, "wait_for_turn", {"timeout_s": timeout})
        if turn.get("status") in ("over",) or turn.get("you_owe"):
            return turn


@pytest.fixture
async def server(tmp_path: Path):
    srv = await start_server(tmp_path)
    yield srv
    await srv.stop()
