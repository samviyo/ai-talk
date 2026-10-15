"""python -m werewolf.housebots --server URL --token T --brain baseline --seed N"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from .brains import BRAINS
from .runner import run_bot


def main() -> int:
    parser = argparse.ArgumentParser(prog="werewolf.housebots")
    parser.add_argument("--server", default=os.environ.get("WW_SERVER"))
    parser.add_argument("--token", default=os.environ.get("WW_TOKEN"))
    parser.add_argument("--brain", default="baseline", choices=sorted(BRAINS))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if not args.server or not args.token:
        parser.error("--server and --token are required (or set WW_SERVER / WW_TOKEN)")
    result = asyncio.run(run_bot(args.server, args.token, args.brain, args.seed))
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
