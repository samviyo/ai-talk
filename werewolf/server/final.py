"""Finale launcher: clone pinned team repos, uv sync, run with a clean env."""

from __future__ import annotations

import asyncio
import os
import random
import secrets
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from werewolf.engine import Config

from .app import create_app
from .state import AppState

READY_BUDGET_S = 90.0
WINDOW_S = 30.0
ENV_ALLOWLIST = ("WW_SERVER", "WW_TOKEN", "PATH", "HOME")


@dataclass
class TeamEntry:
    name: str
    repo: str
    cmd: str


def load_final(path: Path) -> tuple[int, int, list[TeamEntry]]:
    raw = tomllib.loads(path.read_text())
    teams = [TeamEntry(t["name"], t["repo"], t["cmd"]) for t in raw.get("teams", [])]
    return int(raw["seats"]), int(raw.get("seed", 0)), teams


def _clone(repo: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    url, _, commit = repo.partition("#")
    src = Path(url).expanduser()
    if src.exists():
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".venv", ".git"))
        return
    subprocess.run(["git", "clone", url, str(dest)], check=True)
    if commit:
        subprocess.run(["git", "-C", str(dest), "checkout", commit], check=True)


def _install(dest: Path) -> None:
    if (dest / "pyproject.toml").exists():
        subprocess.run(["uv", "sync"], cwd=dest, check=True)


async def run_final(toml_path: Path, check: bool, port: int, budget_usd: float) -> int:
    seats_n, seed, teams = load_final(toml_path)
    if not teams:
        raise SystemExit("final.toml has no [[teams]]")
    bots_dir = Path("runs/final/bots")
    bots_dir.mkdir(parents=True, exist_ok=True)
    checkouts: dict[str, Path] = {}
    for team in teams:
        dest = bots_dir / team.name
        print(f"[final] clone {team.name} <- {team.repo}", flush=True)
        await asyncio.to_thread(_clone, team.repo, dest)
        print(f"[final] install {team.name}", flush=True)
        await asyncio.to_thread(_install, dest)
        checkouts[team.name] = dest

    state = None
    config = Config(
        seats=seats_n, wolves=max(3, seats_n // 4), discussion_rounds=1,
        max_days=6, seed=seed,
    )
    tokens = [secrets.token_urlsafe(12) for _ in range(seats_n)]
    team_names = [t.name for t in teams]
    rng = random.Random(seed)
    assignment = [team_names[i % len(team_names)] for i in range(seats_n)]
    rng.shuffle(assignment)

    from .llm import Prices

    prices_path = Path("prices.toml")
    if not prices_path.exists():
        raise SystemExit("prices.toml not found")
    prices = Prices.load(prices_path)
    state = AppState(
        config=config, n_tables=1, budget_usd=budget_usd, window_s=WINDOW_S,
        runs_dir=Path("runs"), prices=prices, fake_llm=check,
        base_url=os.environ.get("BIFROST_BASE_URL"), teams_toml=None,
        auto_restart=False,
    )
    table = state.tables[0]
    for i, st in enumerate(table.seats):
        st.token = tokens[i]
        st.team = assignment[i]
    state.token_index = {tok: (table, s) for s, tok in enumerate(tokens)}
    table.start_game_locked(seed=seed, need_role_check=True)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(create_app(state), host="127.0.0.1", port=port, log_level="warning")
    )
    serve_task = asyncio.create_task(server.serve())
    await asyncio.sleep(1.0)
    url = f"http://127.0.0.1:{port}"

    if check:
        # smoke-start every bot against the running server, then exit
        smoke_procs = []
        for s in range(seats_n):
            entry = next(t for t in teams if t.name == assignment[s])
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "WW_SERVER": url,
                "WW_TOKEN": tokens[s],
            }
            smoke_procs.append(await asyncio.create_subprocess_shell(
                entry.cmd, cwd=checkouts[entry.name], env=env,
            ))
        try:
            await asyncio.wait_for(table.ready_event.wait(), timeout=READY_BUDGET_S)
            print(f"[final] --check ok: all {seats_n} seats ready", flush=True)
            rc = 0
        except TimeoutError:
            missing = [s for s in range(seats_n) if s not in table.ready_seats]
            print(f"[final] --check FAILED: seats not ready: {missing}", flush=True)
            rc = 1
        for p in smoke_procs:
            if p.returncode is None:
                p.terminate()
        server.should_exit = True
        await asyncio.wait_for(serve_task, timeout=5.0)
        return rc

    procs: dict[int, asyncio.subprocess.Process] = {}
    for s in range(seats_n):
        team_name = assignment[s]
        entry = next(t for t in teams if t.name == team_name)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "WW_SERVER": url,
            "WW_TOKEN": tokens[s],
        }
        print(f"[final] seat {s}: {team_name} ({tokens[s][:6]}…)", flush=True)
        procs[s] = await asyncio.create_subprocess_shell(
            entry.cmd, cwd=checkouts[team_name], env=env,
        )

    async def watch_exit(seat: int, proc: asyncio.subprocess.Process) -> None:
        await proc.wait()
        async with table.cond:
            if table.game.result is None:
                table.seats[seat].process_alive = False
                table.cond.notify_all()
        print(f"[final] seat {seat} process exited", flush=True)

    watchers = [asyncio.create_task(watch_exit(s, p)) for s, p in procs.items()]

    # readiness barrier: every seat calls get_my_role, or 90s elapse
    try:
        await asyncio.wait_for(table.ready_event.wait(), timeout=READY_BUDGET_S)
    except TimeoutError:
        pass
    missing = [s for s in range(seats_n) if s not in table.ready_seats]
    for s in missing:
        print(f"[final] seat {s} not ready; baseline house bot takes over", flush=True)
        proc = procs.get(s)
        if proc and proc.returncode is None:
            proc.terminate()
        async with table.cond:
            table.seats[s].process_alive = True  # house bot isn't broke
        from werewolf.server import patch_mcp_client_compat

        patch_mcp_client_compat()  # housebots' runner predates mcp 2.x
        procs[s] = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "werewolf.housebots",
            "--server", url, "--token", tokens[s], "--brain", "baseline",
            "--seed", str(seed + s),
        )

    # wait for the game to finish: poll the table
    while True:
        async with table.cond:
            done = table.game.result is not None
        if done:
            path = table.write_run()
            print(f"[final] result={table.game.result}; wrote {path}", flush=True)
            break
        await asyncio.sleep(2.0)

    for p in procs.values():
        if p.returncode is None:
            p.terminate()
    for w in watchers:
        w.cancel()
    server.should_exit = True
    await asyncio.wait_for(serve_task, timeout=5.0)
    return 0
