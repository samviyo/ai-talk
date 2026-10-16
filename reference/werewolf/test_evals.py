"""Harness tests over fixture traces, including the always-vote-first-seat control."""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent  # reference/werewolf
sys.path.insert(0, str(ROOT))

from evals import checks  # noqa: E402
from evals.__main__ import evaluate  # noqa: E402
from evals.trace import GameLog, iter_games, load_game  # noqa: E402

FIXTURES = Path(__file__).parents[2] / "starter" / "tests" / "fixtures" / "runs"
THRESHOLDS = tomllib.loads((ROOT / "evals" / "thresholds.toml").read_text())


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


def _wolf_log(chat: str) -> object:
    """Minimal GameLog: wolves 4 and 5, one post by wolf 4."""
    return GameLog(
        seats=7,
        roles={0: "villager", 1: "seer", 2: "doctor", 3: "villager",
               4: "werewolf", 5: "werewolf", 6: "villager"},
        result="wolves",
        events=[{"kind": "post", "day": 1, "round": 1, "seat": 4,
                 "chat": chat, "claimed_role": None, "accuse": None}],
        spend={},
    )


def test_secrecy_flags_first_person_partner_leak():
    log = _wolf_log("I'll be honest, seat 5 is a wolf. I checked.")
    assert not checks.role_secrecy(log, 0).ok


def test_secrecy_flags_wolf_claiming_seer_result_on_partner():
    log = _wolf_log("I am the seer: 5 werewolf.")
    assert not checks.role_secrecy(log, 0).ok


def test_secrecy_ignores_quoted_accusation_of_partner():
    log = _wolf_log("Seat 0 has named seat 5 as a werewolf, which is ridiculous.")
    assert checks.role_secrecy(log, 0).ok


def test_secrecy_ignores_repeated_accusation_of_partner():
    log = _wolf_log("Seat 1 says seat 5 is a wolf. Interesting claim.")
    assert checks.role_secrecy(log, 0).ok


def test_secrecy_catches_wolf_claiming_werewolf():
    _, log = load_game(FIXTURES / "table1" / "game2")
    post = next(
        e for e in log.events
        if e["kind"] == "post" and e["seat"] in log.wolves
    )
    post["claimed_role"] = "werewolf"
    assert not checks.role_secrecy(log, 0).ok


@pytest.mark.parametrize("text", ["I'm a werewolf, deal with it.", "As a wolf I say lynch 3."])
def test_secrecy_flags_wolf_self_reveal(text):
    assert not checks.role_secrecy(_wolf_log(text), 0).ok


@pytest.mark.parametrize("text", ["I'm not a wolf.", "Seat 2 said 'I am a wolf' as a joke."])
def test_secrecy_ignores_denials_and_quotes(text):
    assert checks.role_secrecy(_wolf_log(text), 0).ok


def test_hot_seat_misses_scoped_to_seat():
    log = _wolf_log("hello")
    log.events.append({"kind": "hot_seat_pass", "seat": 3, "day": 1})
    assert checks.hot_seat_misses(log) == 1
    assert checks.hot_seat_misses(log, seats={0}) == 0


def test_offline_gate_fake_decider_is_red():
    proc = subprocess.run(
        [sys.executable, "-m", "evals.offline", "--fake"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert "deduction puzzles" in proc.stdout and "RED" in proc.stdout
    assert proc.returncode != 0


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


def test_seat_scoping_ignores_other_seats_violations():
    """A wolf partner leak by seat 4 must not red a --seat 5 eval of the same game."""
    log = GameLog(
        seats=7,
        roles={0: "villager", 1: "seer", 2: "doctor", 3: "villager",
               4: "werewolf", 5: "werewolf", 6: "villager"},
        result="wolves",
        events=[{"kind": "post", "day": 1, "round": 1, "seat": 4,
                 "chat": "I checked: seat 5 is a wolf.", "claimed_role": None, "accuse": None}],
        spend={},
    )
    assert not checks.role_secrecy(log, 0).ok  # table-wide: leak by wolf 4
    assert checks.role_secrecy(log, 0, seats={5}).ok  # scoped to seat 5: clean


def test_evaluate_seat_scopes_win_rate():
    rows = evaluate(FIXTURES / "table1", THRESHOLDS, seat=4)
    wr = next(r for r in rows if r.name == "win rate by role")
    assert "werewolf" in wr.detail and "villager" not in wr.detail


def test_latest_limits_games():
    from evals.trace import iter_games
    all_games = list(iter_games(FIXTURES))
    one = list(iter_games(FIXTURES, latest=1))
    assert len(all_games) == 3 and len(one) == 1


def test_house_key_excludes_house_seats_by_default(tmp_path):
    """With a house map, the default scope drops house seats (no warning path)."""
    import json as _json
    game = tmp_path / "table1" / "game1"
    game.mkdir(parents=True)
    (game / "trace.jsonl").write_text(
        '{"ts":1,"seat":0,"tool":"vote","args":{"seat":4},"result_code":"ok","latency_ms":1}\n'
    )
    (game / "game.json").write_text(_json.dumps({
        "seats": 3,
        "roles": {"0": "villager", "1": "werewolf", "2": "villager"},
        "result": "village",
        "house": {"2": "baseline"},
        "events": [],
        "spend": {},
    }))
    rows = evaluate(tmp_path, THRESHOLDS)
    label = next(r for r in rows if r.name == "scored seats")
    assert label.detail == "non-house seats"


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
        cwd=ROOT, capture_output=True, text=True,
    )
    assert "RED" in proc.stdout
    assert proc.returncode != 0
    proc2 = subprocess.run(
        [sys.executable, "-m", "evals", "--runs", str(FIXTURES / "table1")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert "RED" not in proc2.stdout
    assert proc2.returncode == 0
