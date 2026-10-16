"""ww CLI: serve, table, tokens, final."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tomllib
from pathlib import Path

import werewolf.server  # noqa: F401

from werewolf.engine import Config

from .llm import Prices
from .state import AppState, DEFAULT_PRICES as _FALLBACK_PRICES

FORMATS = {
    "practice": dict(seats=7, wolves=2, max_days=5, discussion_s=60.0, max_posts=3),
    "final": dict(seats=12, wolves=3, max_days=6, discussion_s=90.0, max_posts=3),
}


def _load_prices() -> Prices:
    path = Path("prices.toml")
    if path.exists():
        return Prices.load(path)
    return Prices(
        input={m: v["input"] for m, v in _FALLBACK_PRICES.items()},
        output={m: v["output"] for m, v in _FALLBACK_PRICES.items()},
    )


def _load_shop() -> tuple[dict[str, float], dict[str, float]]:
    """prices.toml [housebots] and [powerups]; empty when the file is absent."""
    path = Path("prices.toml")
    if not path.exists():
        return {}, {}
    raw = tomllib.loads(path.read_text())
    house = {k: float(v) for k, v in raw.get("housebots", {}).items()}
    powerups = {k: float(v) for k, v in raw.get("powerups", {}).items()}
    return house, powerups


def _load_penalties() -> dict[str, float]:
    """prices.toml [penalties]; hot-seat fines as a fraction of the seat budget."""
    path = Path("prices.toml")
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text())
    return {k: float(v) for k, v in raw.get("penalties", {}).items()}


def _load_teams() -> dict[str, str] | None:
    path = Path("teams.toml")
    if not path.exists():
        return None
    raw = tomllib.loads(path.read_text())
    return {name: v["bifrost_key_env"] for name, v in raw.get("teams", {}).items()}


def build_state(args: argparse.Namespace) -> AppState:
    fmt = FORMATS[args.format]
    discussion_s = getattr(args, "discussion_s", None) or fmt["discussion_s"]
    max_posts = getattr(args, "max_posts", None) or fmt["max_posts"]
    config = Config(
        seats=args.seats or fmt["seats"],
        wolves=fmt["wolves"],
        max_days=fmt["max_days"],
        seed=args.seed,
        discussion_s=discussion_s,
        max_posts=max_posts,
    )
    house_prices, powerup_prices = _load_shop()
    penalties = _load_penalties()
    return AppState(
        config=config,
        n_tables=args.tables,
        budget_usd=args.budget_usd,
        window_s=args.window_s,
        runs_dir=Path(args.runs_dir),
        prices=_load_prices(),
        fake_llm=args.fake_llm,
        base_url=os.environ.get("BIFROST_BASE_URL"),
        teams_toml=_load_teams(),
        min_window_s=args.min_window_s,
        transition_s=args.transition_s,
        house_prices=house_prices or None,
        powerup_prices=powerup_prices or None,
        floor_s=args.floor_s,
        hot_seat_s=args.hot_seat_s,
        hot_seat_answer_s=args.hot_seat_answer_s,
        hot_seat_fine=penalties.get("hot_seat_fine", 0.10),
        reveal_s=args.reveal_s,
        beat_s=args.beat_s,
        manual_start=args.manual_start,
    )


def _print_tokens(state: AppState) -> None:
    print(f"operator token: {state.operator_token}", flush=True)
    for table in state.tables:
        print(f"{table.name}:", flush=True)
        for st in table.seats:
            print(f"  seat {st.seat} ({st.team}): {st.token}", flush=True)


def _write_tokens_file(state: AppState, runs_dir: Path) -> Path:
    """So `ww table` and humans can recover tokens without scraping stdout."""
    import json

    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / "tokens.json"
    path.write_text(
        json.dumps(
            {
                "operator": state.operator_token,
                "tables": {
                    t.name: [st.token for st in t.seats] for t in state.tables
                },
            },
            indent=1,
        )
    )
    return path


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .app import create_app

    state = build_state(args)
    tokens_path = _write_tokens_file(state, Path(args.runs_dir))
    if not args.quiet:
        _print_tokens(state)
        print(f"tokens file: {tokens_path}", flush=True)
    app = create_app(state)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_tokens(args: argparse.Namespace) -> int:
    import json

    path = Path(args.token_file or str(Path(args.runs_dir) / "tokens.json"))
    if path.exists():
        data = json.loads(path.read_text())
        print(f"operator token: {data['operator']}")
        for name, toks in data["tables"].items():
            print(f"{name}:")
            for s, tok in enumerate(toks):
                print(f"  seat {s}: {tok}")
        return 0
    state = build_state(args)
    _print_tokens(state)
    return 0


def cmd_table(args: argparse.Namespace) -> int:
    from werewolf.housebots.runner import run_bot  # noqa: PLC0415

    brains = [b.strip() for b in args.bots.split(",") if b.strip()]
    if not brains:
        raise SystemExit("--bots lists at least one brain")

    async def _run() -> int:
        import json

        import httpx

        token_file = args.token_file or str(Path(args.runs_dir) / "tokens.json")
        data = json.loads(Path(token_file).read_text())
        tokens = data["tables"][f"table{args.table}"]
        operator = data.get("operator")
        # The server, never the client, owns which seats are house bots.
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{args.server}/admin/house",
                params={"op": operator},
                json={str(i): brains[i] for i in range(len(brains))},
                timeout=10,
            )
            resp.raise_for_status()
        tasks = [
            run_bot(args.server, tokens[i], brain, args.seed + i)
            for i, brain in enumerate(brains)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            print(f"seat {i} ({brains[i]}): {r}")
        return 0

    return asyncio.run(_run())


def cmd_final(args: argparse.Namespace) -> int:
    from .final import run_final

    return asyncio.run(
        run_final(
            Path(args.toml), check=args.check, port=args.port,
            budget_usd=args.budget_usd,
            min_window_s=args.min_window_s, transition_s=args.transition_s,
            reveal_s=args.reveal_s, beat_s=args.beat_s,
            manual_start=not args.auto_start,
        )
    )


def cmd_start(args: argparse.Namespace) -> int:
    """Operator: start a manual-start table waiting in the lobby."""
    import json

    import httpx

    token_file = args.token_file or str(Path(args.runs_dir) / "tokens.json")
    operator = json.loads(Path(token_file).read_text()).get("operator")
    resp = httpx.post(
        f"{args.server}/admin/start", params={"op": operator}, timeout=10
    )
    if resp.status_code != 200:
        print(f"start refused: {resp.status_code} {resp.text}")
        return 1
    print(f"game {resp.json()['game']} started")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ww")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--format", choices=sorted(FORMATS), default="practice")
        p.add_argument("--seats", type=int, default=0)
        p.add_argument("--seed", type=int, default=1)
        p.add_argument("--tables", type=int, default=1)
        p.add_argument("--budget-usd", type=float, default=0.50)
        p.add_argument("--window-s", type=float, default=20.0)
        p.add_argument("--min-window-s", type=float, default=0.0)
        p.add_argument("--transition-s", type=float, default=0.0)
        p.add_argument("--runs-dir", default="runs")
        p.add_argument("--fake-llm", action="store_true")
        p.add_argument("--discussion-s", type=float, default=None)
        p.add_argument("--max-posts", type=int, default=None)
        p.add_argument("--floor-s", type=float, default=4.0)
        p.add_argument("--hot-seat-s", type=float, default=6.0)
        p.add_argument("--hot-seat-answer-s", type=float, default=5.0)
        p.add_argument("--reveal-s", type=float, default=1.5)
        p.add_argument("--beat-s", type=float, default=2.5)

    p_serve = sub.add_parser("serve")
    common(p_serve)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--quiet", action="store_true")
    # practice starts as soon as seats are ready; the finale waits for the operator
    p_serve.add_argument(
        "--manual-start",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="hold the lobby until POST /admin/start (default off for serve)",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_tokens = sub.add_parser("tokens")
    p_tokens.add_argument("--token-file", default="")
    common(p_tokens)
    p_tokens.set_defaults(func=cmd_tokens)

    p_table = sub.add_parser("table")
    p_table.add_argument("--server", required=True)
    p_table.add_argument("--table", type=int, default=1)
    p_table.add_argument("--bots", required=True)
    p_table.add_argument("--token-file", default="")
    common(p_table)
    p_table.set_defaults(func=cmd_table)

    p_final = sub.add_parser("final")
    p_final.add_argument("toml")
    p_final.add_argument("--check", action="store_true")
    p_final.add_argument("--port", type=int, default=8000)
    p_final.add_argument("--budget-usd", type=float, default=0.50)
    p_final.add_argument("--min-window-s", type=float, default=10.0)
    p_final.add_argument("--transition-s", type=float, default=6.0)
    p_final.add_argument("--reveal-s", type=float, default=1.5)
    p_final.add_argument("--beat-s", type=float, default=2.5)
    # the finale holds in the lobby until the operator starts it
    p_final.add_argument("--auto-start", action="store_true")
    p_final.set_defaults(func=cmd_final)

    p_start = sub.add_parser("start")
    p_start.add_argument("--server", required=True)
    p_start.add_argument("--token-file", default="")
    p_start.add_argument("--runs-dir", default="runs")
    p_start.set_defaults(func=cmd_start)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
