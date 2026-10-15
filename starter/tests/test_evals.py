"""Harness tests over fixture traces, including the always-vote-first-seat control."""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

STARTER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STARTER))

from evals import checks  # noqa: E402
from evals.__main__ import evaluate  # noqa: E402
from evals.trace import iter_games, load_game  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "runs"
THRESHOLDS = tomllib.loads((STARTER / "evals" / "thresholds.toml").read_text())


def test_fixture_games_load():
    games = list(iter_games(FIXTURES))
    assert {g[0] for g in games} == {
        "table1/game1", "table1/game2", "negctl/game1"
    }


def test_good_game_passes_deterministic_checks():
    traces, log = load_game(FIXTURES / "table1" / "game1")
    assert checks.protocol_errors(traces, 0).ok
    assert checks.reads_board_before_vote(traces, 0.9, log).ok
    assert checks.role_secrecy(log, 0).ok
    assert checks.claim_consistency(log, 0).ok
    assert checks.budget_never_broke(log).ok
    hits, total = checks.vote_accuracy(log)
    assert (hits, total) == (7, 7)
    dealt, identified = checks.seer_follow_through(log)
    assert (dealt, identified) == (2, 2)


def test_negative_control_fails_vote_accuracy():
    """The always-vote-first-seat bot must fail vote accuracy — suite liveness proof."""
    _, log = load_game(FIXTURES / "negctl" / "game1")
    hits, total = checks.vote_accuracy(log)
    assert total > 0
    assert hits / total < THRESHOLDS["min_vote_accuracy"]


def test_negative_control_flags_protocol_errors():
    traces, _ = load_game(FIXTURES / "negctl" / "game1")
    assert not checks.protocol_errors(traces, 0).ok


def test_wolf_game_detects_no_false_secrecy_violation():
    _, log = load_game(FIXTURES / "table1" / "game2")
    assert checks.role_secrecy(log, 0).ok


def test_secrecy_catches_wolf_claiming_werewolf():
    _, log = load_game(FIXTURES / "table1" / "game2")
    post = next(
        e for e in log.events
        if e["kind"] == "post" and e["seat"] in log.wolves
    )
    post["claimed_role"] = "werewolf"
    assert not checks.role_secrecy(log, 0).ok


def test_consistency_catches_changed_claim():
    _, log = load_game(FIXTURES / "table1" / "game2")
    log.events.append({
        "kind": "post", "day": 3, "round": 1, "seat": 4,
        "chat": "ok fine I'm the doctor", "claimed_role": "doctor", "accuse": None,
    })
    assert not checks.claim_consistency(log, 0).ok


def test_scoreboard_exit_codes():
    good = evaluate(FIXTURES / "table1", THRESHOLDS)
    assert all(r.ok for r in good if r.gate)
    bad = evaluate(FIXTURES / "negctl", THRESHOLDS)
    assert any(not r.ok for r in bad if r.gate)


def test_spoof_fixtures_catch_obedient_bot():
    from evals.spoof import run_spoof_fixtures

    # A bot that obeys the injected instruction fails every fixture.
    obeyed, total, _ = run_spoof_fixtures(
        lambda p: json.dumps({"target": 2}) if "vote player 2" in p else json.dumps({"target": 4})
    )
    assert obeyed == total

    def stubborn(_prompt: str) -> str:
        return json.dumps({"target": 99})

    obeyed, total, failed = run_spoof_fixtures(stubborn)
    assert obeyed == 0 and not failed


def test_puzzle_scoring():
    from evals.puzzles import score_answer

    assert score_answer('{"seats": [5]}', [5])
    assert score_answer('{"seats": [1, 5]}', [5, 1])
    assert not score_answer('{"seats": [3]}', [5])
    assert score_answer("seat 5 is lying", [5])


def test_all_five_puzzles_valid_and_answerable():
    from evals.puzzles import PUZZLES_DIR, build_prompt

    puzzles = sorted(PUZZLES_DIR.glob("*.json"))
    assert len(puzzles) == 5
    for path in puzzles:
        pz = json.loads(path.read_text())
        assert {"board_posts", "dead", "question", "answer_seats"} <= pz.keys()
        assert pz["answer_seats"]
        assert pz["question"] in build_prompt(pz)


def test_eval_cli_prints_scoreboard_and_exits_nonzero_on_red():
    proc = subprocess.run(
        [sys.executable, "-m", "evals", "--runs", str(FIXTURES / "negctl")],
        cwd=STARTER, capture_output=True, text=True,
    )
    assert "RED" in proc.stdout
    assert proc.returncode != 0
    proc2 = subprocess.run(
        [sys.executable, "-m", "evals", "--runs", str(FIXTURES / "table1")],
        cwd=STARTER, capture_output=True, text=True,
    )
    assert "RED" not in proc2.stdout
    assert proc2.returncode == 0
