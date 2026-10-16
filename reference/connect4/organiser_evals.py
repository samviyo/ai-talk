"""Eval harness: gates from the talk doc, red/green scoreboard, nonzero exit on failure."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

from connect4 import puzzles
from connect4.bots import column0_bot, random_bot
from connect4.bots.base import load_bot, unwrap
from connect4.play import MoveLog, play_game


def _load_prices() -> dict[str, tuple[float, float]]:
    raw = tomllib.loads((Path(__file__).resolve().parent.parent.parent / "prices.toml").read_text())
    prices = {m: (float(v["input"]), float(v["output"])) for m, v in raw["models"].items()}
    # Unknown models are priced high so a missing row never looks cheap.
    prices["default"] = (4.00, 20.00)
    return prices


# USD per 1M tokens: (prompt, completion), shared with the Werewolf proxy.
PRICES = _load_prices()

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"

WIN_PUZZLE_MIN = 0.95
BLOCK_PUZZLE_MIN = 0.95
BEATS_RANDOM_MIN = 0.95
P95_LATENCY_MAX_S = 3.0
MAX_COST_PER_MOVE_USD = 0.002


def _p95(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * (len(s) - 1) + 0.999999))]


def run_puzzle_suite(bot, kind: str, size: int, seed: int) -> float:
    ps = puzzles.generate(size, kind, seed=seed)
    solved = 0
    for p in ps:
        column, _ = unwrap(bot(p.board, p.player))
        if isinstance(column, int) and not isinstance(column, bool) and column in p.answer:
            solved += 1
    return solved / len(ps) if ps else 0.0


def merge_logs(a: MoveLog, b: MoveLog) -> MoveLog:
    return MoveLog(
        latencies=a.latencies + b.latencies,
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        illegal=a.illegal + b.illegal,
    )


def evaluate(bot, *, games: int, puzzle_size: int, seed: int, model: str | None):
    """Run all gates. Returns (checks, stats); each check is (name, passed, detail)."""
    bot_log = MoveLog()
    wins = draws = 0
    for g in range(games):
        # Alternate who moves first so first-player advantage doesn't skew the rate.
        bots = {1: bot, -1: random_bot.choose} if g % 2 == 0 else {1: random_bot.choose, -1: bot}
        winner, logs = play_game(bots)
        side = 1 if g % 2 == 0 else -1
        bot_log = merge_logs(bot_log, logs[side])
        if winner == side:
            wins += 1
        elif winner == 0:
            draws += 1
    win_rate = wins / games if games else 0.0

    win_p = run_puzzle_suite(bot, "win_in_1", puzzle_size, seed)
    block_p = run_puzzle_suite(bot, "block_in_1", puzzle_size, seed)

    moves = len(bot_log.latencies)
    p95 = _p95(bot_log.latencies)
    cost_per_move = None
    if bot_log.prompt_tokens or bot_log.completion_tokens:
        pp, cp = PRICES.get(model or "default", PRICES["default"])
        total = (sum(bot_log.prompt_tokens) * pp + sum(bot_log.completion_tokens) * cp) / 1e6
        cost_per_move = total / max(moves, 1)

    checks = [
        ("0 illegal moves", bot_log.illegal == 0, f"{bot_log.illegal} illegal over {moves} moves"),
        ("win-in-1 >= 95%", win_p >= WIN_PUZZLE_MIN, f"{win_p:.1%} of {puzzle_size}"),
        ("block-in-1 >= 95%", block_p >= BLOCK_PUZZLE_MIN, f"{block_p:.1%} of {puzzle_size}"),
        ("beats random >= 95%", win_rate >= BEATS_RANDOM_MIN,
         f"{win_rate:.1%} over {games} games ({draws} draws)"),
        ("p95 latency < 3s", p95 < P95_LATENCY_MAX_S, f"p95 {p95:.3f}s over {moves} moves"),
    ]
    if cost_per_move is not None:
        checks.append((
            f"cost/move <= ${MAX_COST_PER_MOVE_USD}",
            cost_per_move <= MAX_COST_PER_MOVE_USD,
            f"${cost_per_move:.5f}/move",
        ))
    stats = {
        "win_rate": win_rate, "win_in_1": win_p, "block_in_1": block_p,
        "illegal": bot_log.illegal, "moves": moves, "p95": p95,
        "cost_per_move": cost_per_move,
    }
    return checks, stats


def negative_control(puzzle_size: int, seed: int) -> tuple[bool, str]:
    """column0_bot must fail the puzzle suite; report if it doesn't."""
    win_p = run_puzzle_suite(column0_bot.choose, "win_in_1", puzzle_size, seed)
    block_p = run_puzzle_suite(column0_bot.choose, "block_in_1", puzzle_size, seed)
    failed = win_p < WIN_PUZZLE_MIN or block_p < BLOCK_PUZZLE_MIN
    return failed, f"column0 win-in-1 {win_p:.1%}, block-in-1 {block_p:.1%}"


def print_scoreboard(title: str, checks: list[tuple[str, bool, str]]) -> bool:
    print(f"\n== {title} ==")
    ok = True
    for name, passed, detail in checks:
        color = GREEN if passed else RED
        print(f"  {color}{'PASS' if passed else 'FAIL'}{RESET}  {name:<26} {detail}")
        ok = ok and passed
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="connect4.evals")
    ap.add_argument("--bot", default="connect4.bots.naive:choose",
                    help="bot spec module:attr")
    ap.add_argument("--games", type=int, default=200,
                    help="games vs random (lower this for LLM bots)")
    ap.add_argument("--puzzles", type=int, default=40, help="puzzles per suite")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--model", default=None, help="model name for cost pricing")
    ap.add_argument("--no-negative-control", action="store_true")
    args = ap.parse_args(argv)

    import os
    model = args.model or os.environ.get("C4_MODEL")
    bot = load_bot(args.bot)

    checks, _ = evaluate(bot, games=args.games, puzzle_size=args.puzzles,
                         seed=args.seed, model=model)
    ok = print_scoreboard(f"eval: {args.bot}", checks)

    if not args.no_negative_control:
        ctrl_failed, detail = negative_control(args.puzzles, args.seed)
        ok = print_scoreboard(
            "negative control: column0_bot must fail puzzles",
            [("suite is live (column0 fails)", ctrl_failed, detail)],
        ) and ok

    print(f"\n{'GREEN' if ok else 'RED'}\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
