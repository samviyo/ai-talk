"""Finale launcher: clone pinned team repos, uv sync, run with a clean env.

A team with no `repo` is a laptop seat: it runs its own bot against this server.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from werewolf.engine import Config

from .app import create_app
from .state import AppState
from .table import DRAIN_S

READY_BUDGET_S = 90.0
WINDOW_S = 30.0
FLOOR_S = 2.5  # 12 seats in a 40s discussion need posts airing faster than the 4s default
MAX_POSTS = 2
ENV_ALLOWLIST = ("WW_SERVER", "WW_TOKEN", "PATH", "HOME")
INSTALL_ENV_ALLOWLIST = ("PATH", "HOME", "UV_CACHE_DIR", "UV_PYTHON", "UV_INDEX_URL")


@dataclass
class TeamEntry:
    name: str
    repo: str = ""
    cmd: str = ""

    @property
    def laptop(self) -> bool:
        return not self.repo


def final_config(seats: int, seed: int) -> tuple[Config, float]:
    """Config and floor_s for a finale table; past 12 seats, days stretch so every post airs."""
    extra = max(0, seats - 12)
    discussion_s = 40.0 + 2.5 * extra
    floor_s = min(FLOOR_S, (discussion_s + DRAIN_S) / (seats * MAX_POSTS))
    config = Config(
        seats=seats, wolves=max(3, seats // 4),
        # At 6 days a third of random-vote 12-seat games drew; past 12 seats wolves rarely win by day 7.
        max_days=7, seed=seed, discussion_s=discussion_s, max_posts=MAX_POSTS,
    )
    return config, floor_s


def load_final(path: Path) -> tuple[int, int, list[TeamEntry]]:
    if not path.exists():
        raise SystemExit(f"{path} not found: write it first (seats, seed, one [[teams]] per team)")
    raw = tomllib.loads(path.read_text())
    teams = [
        TeamEntry(t["name"], t.get("repo", ""), t.get("cmd", "uv run bot"))
        for t in raw.get("teams", [])
    ]
    return int(raw["seats"]), int(raw.get("seed", 0)), teams


def _lan_ip() -> str:
    # UDP connect sends nothing; it only asks the OS which interface routes outward.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


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


async def _launched_ready(table, seats: set[int]) -> None:
    while not seats <= table.ready_seats:
        await asyncio.sleep(0.2)


async def run_final(
    toml_path: Path, check: bool, port: int, budget_usd: float, linger: bool = True,
    min_window_s: float = 10.0, transition_s: float = 6.0,
    reveal_s: float = 1.5, beat_s: float = 2.5, manual_start: bool = True,
    host: str = "127.0.0.1", ready_s: float = READY_BUDGET_S, fake_llm: bool = False,
    forgers: int = 0, runs_dir: Path = Path("runs"),
) -> int:
    fake_llm = fake_llm or check
    seats_n, seed, teams = load_final(toml_path)
    if not teams:
        raise SystemExit("final.toml has no [[teams]]")
    if len(teams) > seats_n:
        raise SystemExit(f"final.toml has {len(teams)} teams but seats = {seats_n}; raise seats")
    bots_dir = runs_dir / "final" / "bots"
    bots_dir.mkdir(parents=True, exist_ok=True)
    checkouts: dict[str, Path] = {}
    for team in teams:
        if team.laptop:
            continue
        dest = bots_dir / team.name
        print(f"[final] clone {team.name} <- {team.repo}", flush=True)
        await asyncio.to_thread(_clone, team.repo, dest)
        print(f"[final] install {team.name}", flush=True)
        await asyncio.to_thread(_install, dest)
        checkouts[team.name] = dest

    teams_toml_path = Path("teams.toml")
    team_names = [t.name for t in teams]
    # Without teams.toml every team shares BIFROST_API_KEY; budgets are per seat either way.
    teams_toml = load_teams_keys(teams_toml_path) if teams_toml_path.exists() else {}
    missing = [] if fake_llm else [
        name for name in team_names
        if not os.environ.get(teams_toml.get(name) or "BIFROST_API_KEY")
    ]
    if missing:
        raise SystemExit(f"no Bifrost key for: {', '.join(missing)} "
                         "(set BIFROST_API_KEY, or the team's var in teams.toml)")

    state = None
    config, floor_s = final_config(seats_n, seed)
    tokens = [secrets.token_urlsafe(12) for _ in range(seats_n)]
    rng = random.Random(seed)
    # One seat per team; baseline house bots fill the rest.
    assignment = team_names + ["house"] * (seats_n - len(team_names))
    rng.shuffle(assignment)
    house_seats = [i for i, name in enumerate(assignment) if name == "house"]
    if forgers > len(house_seats):
        raise SystemExit(f"--forgers {forgers} > {len(house_seats)} house seats; raise seats")
    forger_seats = set(house_seats[:forgers])

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
        runs_dir=runs_dir, prices=prices, fake_llm=fake_llm,
        base_url=os.environ.get("BIFROST_BASE_URL"), teams_toml=teams_toml,
        auto_restart=False, floor_s=floor_s,
        min_window_s=min_window_s, transition_s=transition_s,
        house_prices=house_prices or None, powerup_prices=powerup_prices or None,
        reveal_s=reveal_s, beat_s=beat_s, manual_start=manual_start,
    )
    table = state.tables[0]
    for i, st in enumerate(table.seats):
        st.token = tokens[i]
        st.team = assignment[i]
        if assignment[i] == "house":
            st.house = "forger" if i in forger_seats else "baseline"
    state.token_index = {tok: (table, s) for s, tok in enumerate(tokens)}
    if not manual_start:
        # manual_start leaves the constructor's placeholder lobby game parked
        table.start_game_locked(seed=seed, need_role_check=True)

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(create_app(state), host=host, port=port, log_level="warning")
    )
    serve_task = asyncio.create_task(server.serve())
    await asyncio.sleep(1.0)
    local_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{local_host}:{port}"
    public_url = f"http://{_lan_ip() if host == '0.0.0.0' else host}:{port}"
    from .cli import _write_tokens_file  # noqa: PLC0415

    _write_tokens_file(state, runs_dir / "final")  # `ww start` reads the operator token here
    print(f"[final] spectator: {url}/?op={state.operator_token}", flush=True)
    laptop_names = {t.name for t in teams if t.laptop}
    laptop_seats = {s for s, name in enumerate(assignment) if name in laptop_names}
    if laptop_seats:
        handout = {
            assignment[s]: {"WW_SERVER": public_url, "WW_TOKEN": tokens[s], "seat": s}
            for s in sorted(laptop_seats)
        }
        handout_path = runs_dir / "final" / "laptop_seats.json"
        handout_path.write_text(json.dumps(handout, indent=1))
        print(f"[final] laptop seats ({handout_path}); send each team its command:", flush=True)
        for name, h in handout.items():
            print(f"  {name} (seat {h['seat']}):", flush=True)
            print(f"    WW_SERVER={h['WW_SERVER']} WW_TOKEN={h['WW_TOKEN']} uv run bot", flush=True)

    def bot_cmd(seat: int, house: bool = False) -> tuple[list[str] | str, Path | None, bool]:
        name = assignment[seat]
        if house or name == "house":
            return [
                sys.executable, "-m", "werewolf.housebots",
                # `=` form: token_urlsafe can start with "-", which argparse reads as a flag.
                "--server", url, f"--token={tokens[seat]}", "--brain", table.seats[seat].house,
                "--seed", str(seed + seat),
            ], None, False
        entry = next(t for t in teams if t.name == name)
        return entry.cmd, checkouts[name], True

    async def launch(seat: int, house: bool = False) -> asyncio.subprocess.Process:
        cmd, cwd, shell = bot_cmd(seat, house)
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
        # smoke-start every launched seat against the running server, then exit
        for s in range(seats_n):
            if s in laptop_seats:
                continue
            procs[s] = await launch(s)
            watch_exit(s, procs[s])
        try:
            await asyncio.wait_for(
                _launched_ready(table, set(procs)), timeout=READY_BUDGET_S
            )
            print(f"[final] --check ok: all {len(procs)} launched seats ready", flush=True)
            rc = 0
        except TimeoutError:
            missing = [s for s in procs if s not in table.ready_seats]
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
        where = "laptop" if s in laptop_seats else f"{tokens[s][:6]}…"
        print(f"[final] seat {s}: {assignment[s]} ({where})", flush=True)
        if s in laptop_seats:
            continue
        procs[s] = await launch(s)
        watch_exit(s, procs[s])

    # readiness barrier: every seat calls get_my_role, the operator starts early, or ready_s elapse
    table.fill_missing = table.manual_start
    barrier = [asyncio.ensure_future(table.ready_event.wait()),
               asyncio.ensure_future(table.fill_event.wait())]
    await asyncio.wait(barrier, timeout=ready_s, return_when=asyncio.FIRST_COMPLETED)
    for b in barrier:
        b.cancel()
    async with table.cond:
        missing = [s for s in range(seats_n) if s not in table.ready_seats]
        # Revoke every late seat's token at once, so no late bot checks in mid-replacement.
        for s in missing:
            state.token_index.pop(tokens[s], None)
            tokens[s] = table.seats[s].token = secrets.token_urlsafe(12)
            state.token_index[tokens[s]] = (table, s)
            table.seats[s].house = "baseline"
            table.seats[s].process_alive = True  # house bot isn't broke
    for s in missing:
        print(f"[final] seat {s} not ready; baseline house bot takes over", flush=True)
        proc = procs.get(s)
        if proc and proc.returncode is None:
            await _kill_group(proc)
        procs[s] = await launch(s, house=True)
        watch_exit(s, procs[s])
    if missing and table.fill_event.is_set():
        # The operator already pressed start: give the stand-ins a moment to check in.
        try:
            await asyncio.wait_for(table.ready_event.wait(), timeout=30.0)
        except TimeoutError:
            print("[final] stand-ins slow to check in; starting anyway", flush=True)
    async with table.cond:
        # Open the first window only after readiness/replacement is complete;
        # manual-start holds in the lobby for /admin/start instead.
        table.fill_missing = False  # every seat is filled now; a later start is the plain one
        if not table.manual_start:
            table.unfreeze_locked()
        elif table.fill_event.is_set() and not table._game_started:
            table.start_from_lobby_locked()
            print(f"[final] game {table.game_counter} started", flush=True)
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
