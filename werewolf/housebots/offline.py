"""Offline balance harness: run house-bot brains against the engine without a server.

Simulates one full game per seed by rebuilding each brain's runner-shaped view
from engine events, so balance numbers match the live `ww table` behavior.
Discussion ticks every TICK_S: each living seat decides an opener/reply/talk.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from werewolf.engine import Config, DoctorSave, Game, NightKill, Post, SeerCheck, Vote
from werewolf.housebots.brains import make_brain

TICK_S = 6.0
DISCUSS_TICKS = 5  # 5 ticks of 6s ~ the first 30s of a 60s window


def _night_action(owe: str):
    return {"night_kill": NightKill, "seer_check": SeerCheck,
            "doctor_save": DoctorSave}[owe]


def _to_engine(action: dict, owe: str):
    tool, args = action["tool"], action["args"]
    if tool == "night_action":
        return _night_action(owe)(args["target"])
    if tool == "post":
        return Post(
            args["chat"],
            args.get("claimed_role"),
            args.get("accuse"),
            reply_to=args.get("reply_to"),
            nominate=args.get("nominate"),
            second=args.get("second"),
        )
    if tool == "vote":
        return Vote(args["seat"])
    raise ValueError(f"unknown tool {tool!r}")


def _build_view(game: Game, seat: int) -> dict[str, Any]:
    role = game.role(seat)
    wolves = sorted(
        s for s in range(game.config.seats) if game.role(s) == "werewolf"
    )
    partners = [s for s in wolves if s != seat] if role == "werewolf" else []
    my_checks: dict[int, str] = {}
    dead: dict[int, str] = {}
    board: list[dict] = []
    mentions: list[dict] = []
    if game.alive().__contains__(seat):
        for e in game.events(seat):
            if e.kind == "seer_result" and role == "seer":
                my_checks[e.data["seat"]] = e.data["role"]
            elif e.kind == "death":
                dead[e.data["seat"]] = e.data["role"]
            elif e.kind == "posts_revealed":
                board.extend(e.data["posts"])
            elif e.kind == "mention":
                mentions.append(e.data)
    turn = {
        "turn_id": game.turn_id,
        "phase": game.phase,
        "day": game.day,
        "round": game.round,
        "you_owe": game.pending().get(seat),
        "legal_targets": game.legal_targets(seat) if seat in game.pending() else [],
        "announcements": [],
        "deadline_s": game.window_s(),
    }
    # Offline games have no floor: a granted reply shows up as you_owe="reply".
    reply = game.reply_owed(seat)
    if reply is not None and game.phase == "day_discussion":
        turn = {**turn, "you_owe": "reply", **reply}
    return {
        "seat": seat,
        "role": role,
        "partners": partners,
        "turn": turn,
        "board": board,
        "nominations": game.nominations(),
        "standing": list(game._standing),
        "posts_left": game.posts_left(seat),
        "mentions": mentions,
        "wolf_chat": [],
        "my_checks": my_checks,
        "dead": dead,
    }


def play_game(seat_brains: list[str], seed: int) -> str:
    """Run one game; returns 'village' | 'wolves' | 'draw'."""
    seats = len(seat_brains)
    wolves = 3 if seats >= 10 else 2
    game = Game(Config(seats=seats, wolves=wolves, max_days=5, seed=seed,
                       discussion_s=60.0, max_posts=3))
    brains = [make_brain(name, seed * 100 + i) for i, name in enumerate(seat_brains)]
    while game.result is None:
        if game.phase == "day_discussion":
            _run_discussion(game, brains)
            continue
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


def _run_discussion(game: Game, brains: list) -> None:
    """Tick the live chat: openers first, then replies and table talk."""
    now = 0.0
    opened: set[int] = set()
    for tick in range(DISCUSS_TICKS):
        for seat in game.alive():
            brain = brains[seat]
            view = _build_view(game, seat)
            posts_left = game.posts_left(seat)
            intent = None
            if seat not in opened and now >= brain.opener_delay() and posts_left > 1:
                intent = "opener"
            elif view["mentions"] and posts_left > 0:
                intent = "reply"
            elif seat in opened and posts_left > 1:
                intent = "talk"
            if intent is None:
                continue
            action = brain.decide({**view, "intent": intent})
            if action is None:
                continue
            try:
                game.act(seat, _to_engine(action, "post"), now=now)
                if intent == "opener":
                    opened.add(seat)
            except Exception:
                continue
            buy = brain.maybe_buy(view)
            if buy is not None:
                try:
                    game.apply_powerup(
                        seat, buy["args"]["item"],
                        target=buy["args"].get("target"),
                        msg=buy["args"].get("msg"),
                    )
                except Exception:
                    pass
        now += TICK_S
    game.close_discussion()


def balance(seat_brains: list[str], games: int, base_seed: int = 1000) -> Counter:
    results: Counter = Counter()
    for i in range(games):
        results[play_game(seat_brains, base_seed + i)] += 1
    return results
