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
```

Reads every `runs/<table>/<game>/` pair (`trace.jsonl` + `game.json`) and prints a
red/green scoreboard: protocol errors, reads-before-vote, role secrecy, claim
consistency, vote accuracy (vs the ~33% random baseline), seer follow-through,
budget, and win rate by role. Thresholds are in `evals/thresholds.toml`. Exit is
nonzero if any gate is red.

Deduction puzzles (asks your bot's decide function):

```sh
uv run python -m evals.puzzles --module yourbot.decide   # or: make puzzles
```

## The loop

1. Play practice games against the house bots (`ww serve`, `ww table`).
2. `make eval`. Read the red gates.
3. Fill in `HANDOFF.md`, hand it to your coding agent.
4. Review what the agent changed. It cannot edit `evals/` — that's the point.

## Negative control

`tests/fixtures/runs/` includes an always-vote-first-seat bot's logs. It must fail
vote accuracy. If your suite passes it, your suite is dead.
