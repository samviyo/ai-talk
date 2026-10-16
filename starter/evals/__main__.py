"""Game evals over runs/ transcripts: your scorers + gates, via Braintrust Eval.

    uv run python -m evals                       # runs/practice, non-house seats
    uv run python -m evals --runs runs --latest 5
    uv run python -m evals --runs tests/fixtures/runs/table1 --seat 3

Dataset: one task per (practice game x scored seat); the task is identity because
the games are already played. Teams write scorers as score_* functions in
evals/scorers/*.py and gates in evals/gates.toml. A scorer with no gate is info.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import harness
from .trace import game_views

SCORERS_DIR = Path(__file__).parent / "scorers"
GATES_FILE = Path(__file__).parent / "gates.toml"


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="evals", description="Score your practice transcripts with your suite.")
    ap.add_argument("--runs", type=Path, default=Path("runs/practice"))
    ap.add_argument("--seat", type=int, default=None, help="score only this seat")
    ap.add_argument("--team", type=str, default=None,
                    help="score seats whose game.json team label matches")
    ap.add_argument("--latest", type=int, default=None, metavar="N",
                    help="score only the N most recently modified games")
    ap.add_argument("--scorers", type=Path, default=SCORERS_DIR,
                    help="dir of score_* functions (default evals/scorers)")
    ap.add_argument("--gates", type=Path, default=GATES_FILE,
                    help="gates toml (default evals/gates.toml)")
    args = ap.parse_args()

    scorers = harness.load_scorers(args.scorers)
    gates = harness.load_gates(args.gates)
    project = None
    if os.environ.get("BT_SEND") == "1":
        if not args.team:
            sys.exit("BT_SEND=1 needs --team NAME (project define-finished-NAME)")
        project = f"define-finished-{args.team}"

    if not scorers:
        print("\nSuite:")
        harness.print_scoreboard(
            [harness.Row("no scorers yet", False,
                         "write evals/scorers/*.py (functions named score_*)", True)],
            "RED: no scorers yet: write evals/scorers/*.py",
        )
        sys.exit(1)

    data = game_views(args.runs, seat=args.seat, team=args.team, latest=args.latest)
    if not data:
        print(f"no transcripts with scoreable seats under {args.runs}")
        sys.exit(1)

    rows = harness.run_suite(data, scorers, gates, f"games over {args.runs}",
                             project=project)
    rows.append(harness.Row(
        "tasks", True, f"{len(data)} (game x scored seat) over {args.runs}",
        gate=False))
    rows += harness.negative_control(scorers, gates, project=project)
    sys.exit(harness.print_scoreboard(rows, "Scoreboard"))


if __name__ == "__main__":
    main()
