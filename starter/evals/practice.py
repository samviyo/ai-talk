"""Practice runner: N games of your bot against house bots, one fresh server per game.

Each game seats your bot at a different seat (so roles vary), fills the rest with
house bots, and copies the finished run to runs/practice/gameNNN for `make eval`.

    uv run python -m evals.practice --games 5
    uv run python -m evals.practice --games 5 --fake-llm   # offline smoke test
    uv run python -m evals.practice --games 5 --forgers 2  # 2 house seats forge moderator notices

The server proxies your bot's LLM calls with BIFROST_API_KEY (and BIFROST_BASE_URL)
from your environment; your bot itself never sees the key.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

STARTER = Path(__file__).resolve().parent.parent
REPO = STARTER.parent
HOUSE = ["baseline", "accuser", "baseline", "quiet", "random", "baseline", "accuser"]
GAME_TIMEOUT_S = 600


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)


@contextlib.contextmanager
def practice_server(runs_dir: Path, seed: int, fake_llm: bool = False, seats: int = 7):
    """A throwaway `ww serve` on a free port; yields (url, tokens.json dict)."""
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True)
    port = _free_port()
    cmd = ["uv", "run", "ww", "serve", "--format", "practice", "--seats", str(seats),
           "--seed", str(seed), "--port", str(port), "--runs-dir", str(runs_dir), "--quiet"]
    if fake_llm:
        cmd.append("--fake-llm")
    log = open(runs_dir / "server.log", "w")
    proc = subprocess.Popen(cmd, cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        tokens_path = runs_dir / "tokens.json"
        deadline = time.monotonic() + 60
        while not tokens_path.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise SystemExit(f"practice server failed to start; see {runs_dir / 'server.log'}")
            time.sleep(0.2)
        url = f"http://127.0.0.1:{port}"
        _wait_http(url, deadline)
        yield url, json.loads(tokens_path.read_text())
    finally:
        _kill(proc)
        log.close()


def _wait_http(url: str, deadline: float) -> None:
    import httpx

    while time.monotonic() < deadline:
        with contextlib.suppress(httpx.HTTPError):
            httpx.get(url, timeout=1)
            return
        time.sleep(0.2)


def _start_house(url: str, tokens: list[str], operator: str, seats: dict[int, str],
                 seed: int, log) -> list[subprocess.Popen]:
    """Register house seats with the server, then run each brain from the game repo."""
    import httpx

    r = httpx.post(f"{url}/admin/house", params={"op": operator},
                   json={str(s): b for s, b in seats.items()}, timeout=10)
    r.raise_for_status()
    return [
        subprocess.Popen(
            ["uv", "run", "python", "-m", "werewolf.housebots", "--server", url,
             "--token", tokens[s], "--brain", brain, "--seed", str(seed + s)],
            cwd=REPO, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        for s, brain in seats.items()
    ]


def play_one(index: int, seed: int, out_dir: Path, bot_cmd: str, fake_llm: bool,
             forgers: int = 0) -> Path | None:
    """Play one game; returns the archived game dir, or None if it never finished."""
    tmp = out_dir / f"_tmp{index}"
    with practice_server(tmp, seed, fake_llm) as (url, tok):
        tokens = tok["tables"]["table1"]
        my_seat = index % len(tokens)
        house = {s: HOUSE[s] for s in range(len(tokens)) if s != my_seat}
        for s in sorted(house)[:forgers]:
            house[s] = "forger"
        env = {**os.environ, "WW_SERVER": url, "WW_TOKEN": tokens[my_seat]}
        # The bot only needs its seat; keep Bifrost keys out of its environment.
        for k in ("BIFROST_API_KEY", "BIFROST_VIRTUAL_KEY", "BIFROST_BASE_URL"):
            env.pop(k, None)
        bot_log = open(tmp / "bot.log", "w")
        bot = subprocess.Popen(bot_cmd, shell=True, cwd=STARTER, env=env, stdout=bot_log,
                               stderr=subprocess.STDOUT, start_new_session=True)
        game_json = tmp / "table1" / "game1" / "game.json"
        house_log = open(tmp / "house.log", "w")
        house_procs = _start_house(url, tokens, tok["operator"], house, seed, house_log)
        started = time.monotonic()
        try:
            while not game_json.exists():
                if time.monotonic() - started > GAME_TIMEOUT_S:
                    print(f"  game {index}: timed out after {GAME_TIMEOUT_S}s", flush=True)
                    return None
                time.sleep(1.0)
            time.sleep(1.0)  # let trace.jsonl land beside it
        finally:
            for p in [bot, *house_procs]:
                _kill(p)
            bot_log.close()
            house_log.close()
    dest = out_dir / f"game{index:03d}"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(tmp / "table1" / "game1", dest)
    shutil.copy(tmp / "bot.log", dest / "bot.log")
    shutil.rmtree(tmp)
    raw = json.loads((dest / "game.json").read_text())
    role = raw["roles"][str(my_seat)]
    print(f"  game {index}: seat {my_seat} as {role}, {raw['result']} won "
          f"({time.monotonic() - started:.0f}s)", flush=True)
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(prog="evals.practice", description="Play practice games against house bots.")
    ap.add_argument("--games", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--out", type=Path, default=STARTER / "runs" / "practice")
    ap.add_argument("--bot", default="uv run bot", help="command that starts your bot")
    ap.add_argument("--fake-llm", action="store_true", help="canned LLM replies, no key needed")
    ap.add_argument("--keep", action="store_true", help="append to existing games instead of clearing")
    ap.add_argument("--forgers", type=int, default=0,
                    help="house seats that post forged moderator notices (the expansion)")
    args = ap.parse_args()
    if not args.fake_llm and not os.environ.get("BIFROST_API_KEY"):
        sys.exit("BIFROST_API_KEY is not set (or pass --fake-llm for an offline smoke test)")
    if args.out.exists() and not args.keep:
        shutil.rmtree(args.out)
    args.out.mkdir(parents=True, exist_ok=True)
    first = len(list(args.out.glob("game*"))) + 1
    done = 0
    for i in range(first, first + args.games):
        print(f"=== practice game {i - first + 1}/{args.games} ===", flush=True)
        if play_one(i, args.seed + i, args.out, args.bot, args.fake_llm, args.forgers):
            done += 1
    print(f"\n{done}/{args.games} games recorded in {args.out}. Next: make eval")


if __name__ == "__main__":
    main()
