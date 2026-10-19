# Werewolf team starter kit

Build an LLM Werewolf agent, prove it with evals you write yourself, hand the rest to your coding agent.

## Layout

- `bot/` — minimal MCP-driven agent. Plays legally, naively. Replace its brain.
- `evals/` — the eval harness (Braintrust `Eval()` under the hood, offline by default).
  You write the suite: game scorers in `evals/scorers/*.py`, offline scorers in
  `evals/scorers/offline/*.py`, gates in `evals/gates.toml`. No scorers ship.
- `evals/datasets/` — your offline datasets: `puzzles.jsonl` and `spoofs.jsonl`.
- `tests/` — harness tests and fixture transcripts, including the negative control.

## Run your bot

```sh
export WW_SERVER=http://localhost:8000   # the practice server
export WW_TOKEN=<your seat token>        # from `ww tokens`
uv run bot                               # or: make bot
```

Model choice lives in `bot/config.toml` (`read_board_model`, `decide_model`).
Only models in the server's `prices.toml` are accepted; `max_tokens` is required.

## Evals: the loop

You write the suite; the harness runs it. A *task* is one test case, a *trial* is one
run of a task, a *transcript* is a game's `trace.jsonl` + `game.json` + `bot.log`,
the *outcome* is the final game state, a *scorer* is code returning 0–1 (or None =
not applicable), and a *gate* is a threshold on a scorer's mean (`min_n` in
`evals/gates.toml` sets the minimum applicable trials). Binary pass/fail
scorers are preferred. A capability eval measures what the bot can do; a regression
eval catches what a change broke.

```sh
make practice N=3     # play 3 real games vs house bots into runs/practice/
make eval             # your scorers over every (game, scored seat) task
make check            # offline: your puzzle + spoof datasets through decide()
```

1. `make practice N=3` (needs `BIFROST_API_KEY`; see `evals/practice.py`).
2. Read the transcripts in `runs/practice/` — error analysis. List the failure
   modes, biggest first.
3. Write one scorer per failure mode in `evals/scorers/*.py`, gate each in
   `evals/gates.toml`, and author offline tasks in `evals/datasets/*.jsonl`, scored by
   your own `evals/scorers/offline/*.py` (see its README; each dataset needs a gate).
   Start from the annotated rows in `evals/datasets/examples/` (its README says what
   makes a row fair). `make check` replays your rows with trivial bots and goes RED
   if one clears your gate:

```python
def score_vote_wolf(input, output, expected=None, **kwargs) -> float | bool | None:
    ...
```

4. `make eval`. The scoreboard prints GREEN/RED/info rows with mean and n scored;
   any red gate exits 1. A scorer with no gate is info. The harness also runs your
   suite over a known-bad bot (negative control): if nothing goes red there, RED
   "your suite passes a known-bad bot". It's a smoke test — it shows at least one
   gate catches an obviously bad bot, not that each scorer measures what it claims.
5. When the suite is live (negative control red), `make lock` — agents are now
   blocked from editing `evals/` (a human runs `make unlock`).

Scopes: `make eval RUNS=runs SEAT=6 TEAM=name LATEST=5`, or
`uv run python -m evals --runs tests/fixtures/runs/table1 --seat 3`.
`TRIALS=3 make check` runs 3 trials per task and reports pass rate and pass^k.
`BT_SEND=1 make eval TEAM=wolfpack` sends runs to Braintrust
(project `define-finished-wolfpack`); without it, nothing leaves the machine.
`uv run python -m evals.offline --fake` is the no-LLM smoke test.

Then fill in `HANDOFF.md` and hand it to your coding agent. It can edit `bot/`
but not `evals/` once locked — the suite is the ground truth, so it stays yours.

## Negative control

`tests/fixtures/runs/negctl/` is an always-vote-first-seat bot's transcript. Your
suite must go red over it. If it passes, your suite is dead. Going red there is a
smoke test: it shows at least one gate catches an obviously bad bot; it does not
show each scorer measures what it claims.
