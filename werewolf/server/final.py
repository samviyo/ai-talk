"""Finale launcher: clone pinned team repos, uv sync, run with a clean env."""

from __future__ import annotations

import asyncio
import os
import random
import secrets
import shutil
import signal
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
INSTALL_ENV_ALLOWLIST = ("PATH", "HOME", "UV_CACHE_DIR", "UV_PYTHON", "UV_INDEX_URL")


@dataclass
class TeamEntry:
    name: str
    repo: str
    cmd: str


def load_final(path: Path) -> tuple[int, int, list[TeamEntry]]:
    raw = tomllib.loads(path.read_text())
    teams = [TeamEntry(t["name"], t["repo"], t["cmd"]) for t in raw.get("teams", [])]
    return int(raw["seats"]), int(raw.get("seed", 0)), teams


def load_teams_keys(path: Path) -> dict[str, str]:
    # Same shape as `ww serve`'s teams.toml: team -> Bifrost key env var name.
    raw = tomllib.loads(path.read_text())
    return {name: v["bifrost_key_env"] for name, v in raw.get("teams", {}).items()}


def _clean_env(allow: tuple[str, ...], extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: os.environ[k] for k in allow if k in os.environ}
    env.setdefault("PATH", os.environ.get("PATH", ""))
    env.setdefault("HOME", os.environ.get("HOME", ""))
    if extra:
        env.update(extra)
    return env


def _bot_env(url: str, token: str) -> dict[str, str]:
    return _clean_env(ENV_ALLOWLIST, {"WW_SERVER": url, "WW_TOKEN": token})


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
        # Sanitized env: team build backends must not see Bifrost keys.
        subprocess.run(
            ["uv", "sync"], cwd=dest, check=True, env=_clean_env(INSTALL_ENV_ALLOWLIST)
        )


async def _kill_group(proc: asyncio.subprocess.Process, grace_s: float = 3.0) -> None:
    # Bots run in their own process group; kill the whole group, not the shell.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_s)
    except TimeoutError:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()


async def run_final(
    toml_path: Path, check: bool, port: int, budget_usd: float, linger: bool = True,
    min_window_s: float = 10.0, transition_s: float = 6.0,
    reveal_s: float = 1.5, beat_s: float = 2.5, manual_start: bool = True,
) -> int:
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

    teams_toml_path = Path("teams.toml")
    if not teams_toml_path.exists():
        raise SystemExit("teams.toml not found (finale needs per-team Bifrost keys)")
    teams_toml = load_teams_keys(teams_toml_path)
    team_names = [t.name for t in teams]
    missing = [
        (name, teams_toml.get(name))
        for name in team_names
        if not (teams_toml.get(name) and os.environ.get(teams_toml[name]))
    ]
    if missing:
        names = ", ".join(n for n, _ in missing)
        raise SystemExit(f"teams.toml has no usable key env var for: {names}")

    state = None
    config = Config(
        seats=seats_n, wolves=max(3, seats_n // 4),
        max_days=6, seed=seed, discussion_s=90.0, max_posts=3,
    )
    tokens = [secrets.token_urlsafe(12) for _ in range(seats_n)]
    rng = random.Random(seed)
    # One seat per team; baseline house bots fill the rest.
    assignment = team_names + ["house"] * (seats_n - len(team_names))
    rng.shuffle(assignment)

    from .llm import Prices

    prices_path = Path("prices.toml")
    if not prices_path.exists():
        raise SystemExit("prices.toml not found")
    prices = Prices.load(prices_path)
    raw_prices = tomllib.loads(prices_path.read_text())
    house_prices = {k: float(v) for k, v in raw_prices.get("housebots", {}).items()}
    powerup_prices = {k: float(v) for k, v in raw_prices.get("powerups", {}).items()}
    state = AppState(
        config=config, n_tables=1, budget_usd=budget_usd, window_s=WINDOW_S,
        runs_dir=Path("runs"), prices=prices, fake_llm=check,
        base_url=os.environ.get("BIFROST_BASE_URL"), teams_toml=teams_toml,
        auto_restart=False,
        min_window_s=min_window_s, transition_s=transition_s,
        house_prices=house_prices or None, powerup_prices=powerup_prices or None,
        reveal_s=reveal_s, beat_s=beat_s, manual_start=manual_start,
    )
    table = state.tables[0]
    for i, st in enumerate(table.seats):
        st.token = tokens[i]
        st.team = assignment[i]
        if assignment[i] == "house":
            st.house = "baseline"  # finale house seats run the baseline brain
    state.token_index = {tok: (table, s) for s, tok in enumerate(tokens)}
    if not manual_start:
        # manual_start leaves the constructor's placeholder lobby game parked
        table.start_game_locked(seed=seed, need_role_check=True)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(create_app(state), host="127.0.0.1", port=port, log_level="warning")
    )
    serve_task = asyncio.create_task(server.serve())
    await asyncio.sleep(1.0)
    url = f"http://127.0.0.1:{port}"
    print(f"[final] spectator: {url}/?op={state.operator_token}", flush=True)

    def bot_cmd(seat: int) -> tuple[list[str] | str, Path | None, bool]:
        name = assignment[seat]
        if name == "house":
            return [
                sys.executable, "-m", "werewolf.housebots",
                "--server", url, "--token", tokens[seat], "--brain", "baseline",
                "--seed", str(seed + seat),
            ], None, False
        entry = next(t for t in teams if t.name == name)
        return entry.cmd, checkouts[name], True

    async def launch(seat: int) -> asyncio.subprocess.Process:
        cmd, cwd, shell = bot_cmd(seat)
        env = _bot_env(url, tokens[seat])
        if shell:
            return await asyncio.create_subprocess_shell(
                cmd, cwd=cwd, env=env, start_new_session=True,
            )
        return await asyncio.create_subprocess_exec(
            *cmd, env=env, start_new_session=True,
        )

    procs: dict[int, asyncio.subprocess.Process] = {}
    watchers: dict[int, asyncio.Task] = {}

    def watch_exit(seat: int, proc: asyncio.subprocess.Process) -> None:
        async def _w() -> None:
            await proc.wait()
            async with table.cond:
                # Only the process this watcher was attached to may flip the flag.
                if procs.get(seat) is proc and table.game.result is None:
                    table.seats[seat].process_alive = False
                    table.cond.notify_all()
            print(f"[final] seat {seat} process exited", flush=True)

        watchers[seat] = asyncio.ensure_future(_w())

    if check:
        # smoke-start every seat against the running server, then exit
        for s in range(seats_n):
            procs[s] = await launch(s)
            watch_exit(s, procs[s])
        try:
            await asyncio.wait_for(table.ready_event.wait(), timeout=READY_BUDGET_S)
            print(f"[final] --check ok: all {seats_n} seats ready", flush=True)
            rc = 0
        except TimeoutError:
            missing = [s for s in range(seats_n) if s not in table.ready_seats]
            print(f"[final] --check FAILED: seats not ready: {missing}", flush=True)
            rc = 1
        for p in procs.values():
            if p.returncode is None:
                await _kill_group(p)
        for w in watchers.values():
            w.cancel()
        server.should_exit = True
        await asyncio.wait_for(serve_task, timeout=5.0)
        return rc

    for s in range(seats_n):
        print(f"[final] seat {s}: {assignment[s]} ({tokens[s][:6]}…)", flush=True)
        procs[s] = await launch(s)
        watch_exit(s, procs[s])

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
            await _kill_group(proc)
        async with table.cond:
            table.seats[s].process_alive = True  # house bot isn't broke
        procs[s] = await launch(s)
        watch_exit(s, procs[s])
    async with table.cond:
        # Open the first window only after readiness/replacement is complete;
        # manual-start holds in the lobby for /admin/start instead.
        if not table.manual_start:
            table.unfreeze_locked()
        table.cond.notify_all()

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
            await _kill_group(p)
    for w in watchers.values():
        w.cancel()
    if linger:
        # Keep the projector on the final board until the operator stops it.
        print("[final] game over; spectator stays up, Ctrl-C to exit", flush=True)
        await serve_task
        return 0
    server.should_exit = True
    await asyncio.wait_for(serve_task, timeout=5.0)
    return 0
