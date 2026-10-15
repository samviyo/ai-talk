# Werewolf team starter kit

Build an LLM Werewolf agent, prove it with evals, hand the rest to your coding agent.

## Layout

- `bot/` — minimal MCP-driven agent. Plays legally, naively. Replace its brain.
- `evals/` — the check suite. **Read-only for agents** (a PreToolUse hook enforces it).
  Add *new* puzzle files under `evals/puzzles/` yourself if you want more coverage —
  but agents are blocked from touching anything here.
- `evals/puzzles/` — deduction puzzles with known answers.
- `tests/` — harness tests and fixture traces, including the negative control.
- `HANDOFF.md` — the prompt you give your coding agent, plus a Definition of Finished.

## Run your bot

```sh
export WW_SERVER=http://localhost:8000   # the practice server
export WW_TOKEN=<your seat token>        # from `ww tokens`
uv run bot                               # or: make bot
```

Model choice lives in `bot/config.toml` (`read_board_model`, `decide_model`).
Only models in the server's `prices.toml` are accepted; `max_tokens` is required.

## Run the evals

```sh
uv run python -m evals --runs runs       # or: make eval
make eval SEAT=6                          # score only your seat
uv run python -m evals --runs runs --team wolfpack   # or by game.json team label
uv run python -m evals --runs runs --latest 5        # only the 5 most recent games
```

Reads every `runs/<table>/<game>/` pair (`trace.jsonl` + `game.json`) and prints a
red/green scoreboard: protocol errors, reads-before-vote, role secrecy, claim
consistency, vote accuracy (vs the ~33% random baseline), seer follow-through,
budget, and win rate by role. Thresholds are in `evals/thresholds.toml`. Exit is
nonzero if any gate is red.

By default the scoreboard covers every seat, house bots included. If `game.json`
marks house seats (a `house: {"<seat>": "<brain>"}` map), only non-house seats are
scored; otherwise it warns and scores the whole table. Use `--seat`/`--team` to
score just your bot — house bots can trip table-wide gates your bot can't control.

Deduction puzzles (asks your bot's decide function):

```sh
uv run python -m evals.puzzles --module yourbot.decide   # or: make puzzles
uv run python -m evals.puzzles --direct --model claude-haiku-5-5   # maintainer: gateway direct
uv run python -m evals.puzzles --fake                              # offline harness self-test
```

`--direct` needs `BIFROST_BASE_URL` + `BIFROST_API_KEY` (source `../scripts/gateway-env.sh`)
and calls the gateway with `temperature=0` so the score is a stable signal, not sampling
noise. `--fake` runs the harness offline for tests.

## The loop

`ww table` runs one game and exits. To practice N games, loop it sequentially:

```sh
make practice N=5      # runs ww table N times, one game each
```

Then iterate:

1. `make practice N=5` to play practice games against the house bots.
2. `make eval-fresh` to archive old runs (`runs/archive/`) so a stale batch never
   pollutes the next scoreboard, then play a fresh batch.
3. `make eval SEAT=<yours>`. Read the red gates.
4. Fill in `HANDOFF.md`, hand it to your coding agent.
5. Review what the agent changed. It cannot edit `evals/` — that's the point.

## Negative control

`tests/fixtures/runs/` includes an always-vote-first-seat bot's logs. It must fail
vote accuracy. If your suite passes it, your suite is dead.
