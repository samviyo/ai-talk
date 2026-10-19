"""Round-robin tournament between bot specs; prints a standings table."""

from __future__ import annotations

import argparse
import itertools
import sys

from .bots.base import load_bot
from .play import play_game


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="connect4.tournament")
    ap.add_argument("bots", nargs="+", help="bot specs, e.g. connect4.bots.random_bot:choose")
    ap.add_argument("--games", type=int, default=2, help="games per pairing, split across colors (rounds down to even, min 2)")
    args = ap.parse_args(argv)

    specs = args.bots
    names = {s: s.rsplit(":", 1)[0].rsplit(".", 1)[-1] for s in specs}
    wins = {s: 0 for s in specs}
    draws = {s: 0 for s in specs}
    losses = {s: 0 for s in specs}

    for a, b in itertools.permutations(specs, 2):
        bots = {1: load_bot(a), -1: load_bot(b)}
        for _ in range(max(1, args.games // 2)):
            winner, _ = play_game(bots)
            if winner == 1:
                wins[a] += 1
                losses[b] += 1
            elif winner == -1:
                wins[b] += 1
                losses[a] += 1
            else:
                draws[a] += 1
                draws[b] += 1

    table = sorted(specs, key=lambda s: (-wins[s], -draws[s], losses[s]))
    width = max(len(names[s]) for s in specs)
    print(f"{'bot':<{width}}  {'W':>4}  {'D':>4}  {'L':>4}  {'score':>6}")
    for s in table:
        score = wins[s] + 0.5 * draws[s]
        print(f"{names[s]:<{width}}  {wins[s]:>4}  {draws[s]:>4}  {losses[s]:>4}  {score:>6.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
