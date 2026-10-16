"""Scoreboard over runs/<table>/<game>/ traces. Nonzero exit on any gate failure."""
from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import checks
from .trace import iter_games


@dataclass
class Row:
    name: str
    ok: bool
    detail: str
    gate: bool = True


def _seats_for(log, seat: int | None, team: str | None) -> set[int] | None:
    """Resolve which seats to score. None means all seats (table-wide)."""
    if seat is not None:
        return {seat}
    if team is not None:
        # game.json seats may carry a team label per seat; fall back to non-house.
        teamed = _team_seats(log, team)
        if teamed:
            return teamed
    if log.house:
        return set(log.roles) - set(log.house)
    return None


def _team_seats(log, team: str) -> set[int]:
    """Seats whose game.json team label matches; empty if no team data recorded."""
    return {s for s, t in log.teams.items() if t == team}


def evaluate(runs_dir: Path, thresholds: dict, seat: int | None = None,
             team: str | None = None, latest: int | None = None) -> list[Row]:
    games = list(iter_games(runs_dir, latest=latest))
    rows: list[Row] = []
    if not games:
        return [Row("games found", False, f"none under {runs_dir}")]

    # Resolve the seat scope once per game; warn if scoring table-wide by default.
    scopes: list[set[int] | None] = []
    for _, _, log in games:
        scope = _seats_for(log, seat, team)
        scopes.append(scope)

    scoped = seat is not None or team is not None
    if not scoped and any(s is not None for s in scopes):
        scope_label = "non-house seats"
    elif not scoped:
        scope_label = "all seats (no house data — table-wide)"
        print(
            "warning: game.json has no 'house' key; scoring all seats, house bots "
            "included. Pass --seat N or --team NAME to scope to your bot.",
            file=sys.stderr,
        )
    else:
        scope_label = f"seat {seat}" if seat is not None else f"team {team}"

    # Protocol errors, aggregated over all games.
    def proto(t, s):
        return checks.protocol_errors(t, thresholds["max_protocol_errors"], seats=s).ok

    total_err = sum(0 if proto(t, s) else 1 for (_, t, _), s in zip(games, scopes))
    rows.append(Row(
        "protocol errors",
        all(proto(t, s) for (_, t, _), s in zip(games, scopes)),
        f"{total_err}/{len(games)} games over the limit",
    ))

    # Reads board before voting.
    fracs = []
    for (_, traces, log), s in zip(games, scopes):
        r = checks.reads_board_before_vote(traces, 0.0, log, seats=s)
        if r.detail != "no votes":
            n, d = r.detail.split(" votes")[0].split("/")
            fracs.append(int(n) / int(d))
    frac = sum(fracs) / len(fracs) if fracs else 1.0
    rows.append(Row(
        "reads board before voting",
        frac >= thresholds["min_reads_before_vote"],
        f"{frac:.0%} of votes preceded by read_board",
    ))

    # Role secrecy.
    violations = sum(
        0 if checks.role_secrecy(log, thresholds["max_role_secrecy_violations"], seats=s).ok else 1
        for (_, _, log), s in zip(games, scopes)
    )
    rows.append(Row(
        "role secrecy",
        violations == 0,
        f"{violations}/{len(games)} games with violations",
    ))

    # Claim consistency.
    inconsistent = sum(
        0 if checks.claim_consistency(log, thresholds["max_claim_inconsistencies"], seats=s).ok else 1
        for (_, _, log), s in zip(games, scopes)
    )
    rows.append(Row(
        "claim consistency",
        inconsistent == 0,
        f"{inconsistent}/{len(games)} games with contradictions",
    ))

    # Vote accuracy vs the random baseline.
    hits = total = 0
    for (_, _, log), s in zip(games, scopes):
        h, t = checks.vote_accuracy(log, seats=s)
        hits += h
        total += t
    enough = len(games) >= thresholds["min_vote_accuracy_games"]
    acc = hits / total if total else 0.0
    rows.append(Row(
        "vote accuracy",
        (acc >= thresholds["min_vote_accuracy"]) if enough else True,
        f"{hits}/{total} = {acc:.0%} (random ~33%, gate {thresholds['min_vote_accuracy']:.0%})"
        + ("" if enough else f" — skipped, need {thresholds['min_vote_accuracy_games']} games"),
        gate=enough,
    ))

    # Seer follow-through.
    dealt = identified = 0
    for (_, _, log), s in zip(games, scopes):
        d, i = checks.seer_follow_through(log, seats=s)
        dealt += d
        identified += i
    ft = dealt / identified if identified else 1.0
    rows.append(Row(
        "seer follow-through",
        ft >= thresholds["min_seer_follow_through"] or identified == 0,
        f"{dealt}/{identified} identified wolves voted out within a day"
        + ("" if identified else " — no seer identifications"),
    ))

    # Budget: nobody broke.
    broke_games = sum(
        0 if checks.budget_never_broke(log, seats=s).ok else 1
        for (_, _, log), s in zip(games, scopes)
    )
    rows.append(Row(
        "budget never broke",
        broke_games <= thresholds["max_broke_games"],
        f"{broke_games}/{len(games)} games with a broke seat",
    ))

    # Nominations: a day with no nominations never reaches a vote.
    hits = seat_days = 0
    for (_, _, log), s in zip(games, scopes):
        h, d = checks.nominations_per_day(log, seats=s)
        hits += h
        seat_days += d
    rate = hits / seat_days if seat_days else 1.0
    has_noms = any(
        "nominate" in e for _, _, log in games for e in log.events if e["kind"] == "post"
    )
    detail = f"{hits}/{seat_days} living seat-days = {rate:.0%} (gate {thresholds['min_nominate_rate']:.0%})"
    rows.append(Row(
        "nominates or seconds",
        rate >= thresholds["min_nominate_rate"] if has_noms else True,
        detail if has_noms else "skipped, logs predate nominations",
        gate=has_noms,
    ))

    # Hot seat: answer within the deadline or pay a fine.
    misses = [checks.hot_seat_misses(log, seats=s) for (_, _, log), s in zip(games, scopes)]
    worst = max(misses, default=0)
    rows.append(Row(
        "hot seat answered",
        worst <= thresholds["max_hot_seat_misses_per_game"],
        f"{sum(misses)} misses over {len(games)} games (max {thresholds['max_hot_seat_misses_per_game']}/game)",
    ))

    # Spend, informational: what a game costs your seat.
    spent = [
        sum(v for k, v in log.spend.items() if s is None or k in s) / max(1, len(s) if s else log.seats)
        for (_, _, log), s in zip(games, scopes)
    ]
    rows.append(Row(
        "spend per seat-game", True,
        f"avg ${sum(spent) / len(spent):.4f}, max ${max(spent):.4f}" if spent else "no games",
        gate=False,
    ))

    # Win rate by role — informational until enough games.
    wins_by_role: dict[str, list[int]] = {}
    for (_, _, log), s in zip(games, scopes):
        for seat_i, role in log.roles.items():
            if s is not None and seat_i not in s:
                continue
            side = "wolves" if role == "werewolf" else "village"
            won = int(log.result == side)
            wins_by_role.setdefault(role, []).append(won)
    enough_wr = len(games) >= thresholds["min_games_for_win_rate"]
    detail = ", ".join(
        f"{role} {sum(w)}/{len(w)}" for role, w in sorted(wins_by_role.items())
    ) or "no games"
    rows.append(Row(
        "win rate by role",
        True,
        detail + ("" if enough_wr else " (informational)"),
        gate=False,
    ))
    rows.append(Row("scored seats", True, scope_label, gate=False))
    return rows


def print_scoreboard(rows: list[Row]) -> int:
    width = max(len(r.name) for r in rows)
    failed = 0
    for r in rows:
        if r.gate:
            mark = "GREEN" if r.ok else "RED  "
            failed += 0 if r.ok else 1
        else:
            mark = "info "
        print(f"  [{mark}] {r.name:<{width}}  {r.detail}")
    print(f"\n{len([r for r in rows if r.gate]) - failed} gates green, {failed} red")
    return 1 if failed else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Werewolf eval scoreboard")
    ap.add_argument("--runs", type=Path, default=Path("runs"))
    ap.add_argument(
        "--thresholds",
        type=Path,
        default=Path(__file__).parent / "thresholds.toml",
    )
    ap.add_argument("--seat", type=int, default=None,
                    help="score only this seat")
    ap.add_argument("--team", type=str, default=None,
                    help="score seats whose game.json team label matches")
    ap.add_argument("--latest", type=int, default=None, metavar="N",
                    help="score only the N most recently modified games")
    args = ap.parse_args()
    thresholds = tomllib.loads(args.thresholds.read_text())
    rows = evaluate(args.runs, thresholds, seat=args.seat, team=args.team,
                    latest=args.latest)
    print(f"\nScoreboard over {args.runs}:\n")
    sys.exit(print_scoreboard(rows))


if __name__ == "__main__":
    main()
