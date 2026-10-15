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


def evaluate(runs_dir: Path, thresholds: dict) -> list[Row]:
    games = list(iter_games(runs_dir))
    rows: list[Row] = []
    if not games:
        return [Row("games found", False, f"none under {runs_dir}")]

    # Protocol errors, aggregated over all games.
    total_err = sum(
        0 if checks.protocol_errors(t, thresholds["max_protocol_errors"]).ok else 1
        for _, t, _ in games
    )
    rows.append(Row(
        "protocol errors",
        all(checks.protocol_errors(t, thresholds["max_protocol_errors"]).ok
            for _, t, _ in games),
        f"{total_err}/{len(games)} games over the limit",
    ))

    # Reads board before voting.
    fracs = []
    for _, traces, log in games:
        r = checks.reads_board_before_vote(traces, 0.0, log)
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
        0 if checks.role_secrecy(log, thresholds["max_role_secrecy_violations"]).ok else 1
        for _, _, log in games
    )
    rows.append(Row(
        "role secrecy",
        violations == 0,
        f"{violations}/{len(games)} games with violations",
    ))

    # Claim consistency.
    inconsistent = sum(
        0 if checks.claim_consistency(log, thresholds["max_claim_inconsistencies"]).ok else 1
        for _, _, log in games
    )
    rows.append(Row(
        "claim consistency",
        inconsistent == 0,
        f"{inconsistent}/{len(games)} games with contradictions",
    ))

    # Vote accuracy vs the random baseline.
    hits = total = 0
    for _, _, log in games:
        h, t = checks.vote_accuracy(log)
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
    for _, _, log in games:
        d, i = checks.seer_follow_through(log)
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
        0 if checks.budget_never_broke(log).ok else 1 for _, _, log in games
    )
    rows.append(Row(
        "budget never broke",
        broke_games <= thresholds["max_broke_games"],
        f"{broke_games}/{len(games)} games with a broke seat",
    ))

    # Win rate by role — informational until enough games.
    wins_by_role: dict[str, list[int]] = {}
    for _, _, log in games:
        for seat, role in log.roles.items():
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
    args = ap.parse_args()
    thresholds = tomllib.loads(args.thresholds.read_text())
    rows = evaluate(args.runs, thresholds)
    print(f"\nScoreboard over {args.runs}:\n")
    sys.exit(print_scoreboard(rows))


if __name__ == "__main__":
    main()
