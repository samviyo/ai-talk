"""Deduction puzzles: replay a board state to the bot's decide function.

Puzzle file format (evals/puzzles/*.json):
  {
    "board_posts": [{"seat": int, "chat": str, "claimed_role": str|null, "day": int}],
    "dead": [int],                # seats already eliminated, with roles known
    "dead_roles": {"3": "villager"},
    "question": str,              # e.g. "3 and 5 both claim seer. Who is lying?"
    "answer_seats": [int]         # seats a correct answer must name
  }

Run `python -m evals.puzzles --module yourbot.decide` to score a decide function.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from pathlib import Path

PUZZLES_DIR = Path(__file__).parent / "puzzles"


def build_prompt(puzzle: dict) -> str:
    posts = "\n".join(
        f"  seat {p['seat']} (day {p.get('day', 1)}): {p['chat']}"
        + (f" [claims {p['claimed_role']}]" if p.get("claimed_role") else "")
        for p in puzzle["board_posts"]
    )
    dead = ", ".join(
        f"seat {s} ({r})" for s, r in puzzle.get("dead_roles", {}).items()
    ) or "none"
    return (
        f"Werewolf deduction. Dead so far: {dead}.\n\nBoard posts:\n{posts}\n\n"
        f"Question: {puzzle['question']}\n"
        'Reply with JSON: {"seats": [<seat numbers>]}.'
    )


def score_answer(reply: str, answer_seats: list[int]) -> bool:
    try:
        seats = [int(s) for s in json.loads(reply)["seats"]]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        seats = [int(m) for m in re.findall(r"\b\d+\b", reply)]
    return sorted(set(seats)) == sorted(answer_seats)


def run(decide_fn, puzzles_dir: Path = PUZZLES_DIR) -> tuple[int, int]:
    puzzles = sorted(puzzles_dir.glob("*.json"))
    correct = 0
    for path in puzzles:
        puzzle = json.loads(path.read_text())
        reply = decide_fn(build_prompt(puzzle))
        ok = score_answer(reply, puzzle["answer_seats"])
        correct += ok
        print(f"  [{'ok' if ok else 'X '}] {path.stem}")
    return correct, len(puzzles)


def _direct_decide(model: str):
    """Call the gateway directly (maintainer path) with BIFROST_BASE_URL/_API_KEY."""
    import os

    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ["BIFROST_BASE_URL"].rstrip("/") + "/v1",
        api_key=os.environ["BIFROST_API_KEY"],
        default_headers={"x-bf-vk": os.environ["BIFROST_API_KEY"]},  # Bifrost dev ignores Bearer and only checks this header
        max_retries=0,
    )

    def decide(prompt: str) -> str:
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            temperature=0,  # puzzles must be deterministic to be a stable signal
            messages=[{"role": "user", "content": prompt}],
        )
        return (resp.choices[0].message.content or "").strip()

    return decide


def _fake_decide(prompt: str) -> str:
    """Offline canned answer: name the first seat a puzzle expects, for tests."""
    seats = re.findall(r"\b\d+\b", prompt)
    return json.dumps({"seats": [int(seats[-1])]}) if seats else '{"seats": []}'


def main() -> None:
    ap = argparse.ArgumentParser(description="Score deduction puzzles")
    ap.add_argument(
        "--module",
        default="bot.puzzle_decide",
        help="module exposing decide(prompt) -> str",
    )
    ap.add_argument("--puzzles", type=Path, default=PUZZLES_DIR)
    ap.add_argument("--direct", action="store_true",
                    help="call the gateway directly via BIFROST_BASE_URL/_API_KEY")
    ap.add_argument("--model", default="claude-haiku-5-5",
                    help="model for --direct (match the bot's decide model)")
    ap.add_argument("--fake", action="store_true",
                    help="offline canned answers, for harness tests")
    args = ap.parse_args()
    if args.fake:
        decide_fn = _fake_decide
    elif args.direct:
        decide_fn = _direct_decide(args.model)
    else:
        mod = importlib.import_module(args.module)
        decide_fn = mod.decide
    correct, total = run(decide_fn, args.puzzles)
    print(f"\n{correct}/{total} puzzles correct")
    sys.exit(0 if total and correct == total else 1)


if __name__ == "__main__":
    main()
