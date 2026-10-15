"""Offline balance harness: run house-bot brains against the engine without a server.

Simulates one full game per seed by rebuilding each brain's runner-shaped view
from engine events, so balance numbers match the live `ww table` behavior.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from werewolf.engine import Config, DoctorSave, Game, NightKill, Post, SeerCheck, Vote
from werewolf.housebots.brains import make_brain

def _night_action(owe: str):
    return {"night_kill": NightKill, "seer_check": SeerCheck,
            "doctor_save": DoctorSave}[owe]


def _to_engine(action: dict, owe: str):
    tool, args = action["tool"], action["args"]
    if tool == "night_action":
        return _night_action(owe)(args["target"])
    if tool == "post":
        return Post(args["chat"], args.get("claimed_role"), args.get("accuse"))
    if tool == "vote":
        return Vote(args["seat"])
    raise ValueError(f"unknown tool {tool!r}")


def _reveal_post_events(game: Game) -> dict[int, list[dict]]:
    """Group revealed public posts by the day/round they were made."""
    revealed: dict[int, list[dict]] = {}
    for e in game.events(None):
        if e.kind == "posts_revealed":
            for p in e.data["posts"]:
                revealed.setdefault(p["seat"], []).append(p)
    return revealed


def _build_view(game: Game, seat: int) -> dict[str, Any]:
    role = game.role(seat)
    wolves = sorted(
        s for s in range(game.config.seats) if game.role(s) == "werewolf"
    )
    partners = [s for s in wolves if s != seat] if role == "werewolf" else []
    my_checks: dict[int, str] = {}
    dead: dict[int, str] = {}
    board: list[dict] = []
    if game.alive().__contains__(seat):
        for e in game.events(seat):
            if e.kind == "seer_result" and role == "seer":
                my_checks[e.data["seat"]] = e.data["role"]
            elif e.kind == "death":
                dead[e.data["seat"]] = e.data["role"]
            elif e.kind == "posts_revealed":
                board.extend(e.data["posts"])
    turn = {
        "turn_id": game.turn_id,
        "phase": game.phase,
        "day": game.day,
        "round": game.round,
        "you_owe": game.pending().get(seat),
        "legal_targets": game.legal_targets(seat) if seat in game.pending() else [],
        "announcements": [],
        "deadline_s": 20,
    }
    return {
        "seat": seat,
        "role": role,
        "partners": partners,
        "turn": turn,
        "board": board,
        "wolf_chat": [],
        "my_checks": my_checks,
        "dead": dead,
    }


def play_game(seat_brains: list[str], seed: int) -> str:
    """Run one game; returns 'village' | 'wolves' | 'draw'."""
    game = Game(Config(seats=len(seat_brains), wolves=2, discussion_rounds=2,
                       max_days=5, seed=seed))
    brains = [make_brain(name, seed * 100 + i) for i, name in enumerate(seat_brains)]
    while game.result is None:
        for seat in sorted(game.pending()):
            if seat not in game.alive():
                game.abstain(seat)
                continue
            view = _build_view(game, seat)
            action = brains[seat].decide(view)
            if action is None:
                game.abstain(seat)
                continue
            game.act(seat, _to_engine(action, game.pending()[seat]))
    return game.result


def balance(seat_brains: list[str], games: int, base_seed: int = 1000) -> Counter:
    results: Counter = Counter()
    for i in range(games):
        results[play_game(seat_brains, base_seed + i)] += 1
    return results
