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

## Evals: the loop

Three layers, fastest first. All three are red/green with a nonzero exit on any red gate.

```sh
make check            # ~30s, no server: deduction puzzles + refuses spoofed moderator posts
make practice N=5     # N real games: your bot vs 6 house bots, a fresh server per game
make eval             # scoreboard over runs/practice (your seat only; house bots excluded)
```

`make check` calls `decide(prompt)` in `bot/puzzle_decide.py` with your bot's system
prompt and model at `temperature=0`. If you change how your bot thinks, keep that file
pointing at the same prompt and model, or the score stops describing your bot.

`make practice` seats your bot at a different seat each game, so you see every role.
It needs `BIFROST_API_KEY` and `BIFROST_BASE_URL` (`source ../scripts/bifrost-env.sh`);
the practice server proxies your LLM calls and your bot never sees the key. One game
takes 3–10 minutes. Each lands in `runs/practice/gameNNN/` with `trace.jsonl`,
`game.json` and `bot.log`. `make practice N=5 KEEP=1` appends instead of clearing.
`--fake-llm` (via `uv run python -m evals.practice --fake-llm`) is an offline smoke test.

The scoreboard gates:

| Gate | What it catches |
|---|---|
| protocol errors | bad args, acting out of turn, spamming past the call budget |
| reads board before voting | voting blind |
| role secrecy | a wolf naming its partner or saying "I'm a wolf" |
| claim consistency | claiming two different roles |
| vote accuracy | town votes no better than random (needs 5 games) |
| seer follow-through | the seer finds a wolf and the town ignores it |
| budget never broke | running out of money |
| nominates or seconds | no nominations means no vote that day |
| hot seat answered | missing the hot-seat deadline costs 10% of the budget |

Spend per game and win rate by role are informational. Thresholds live in
`evals/thresholds.toml`.

Other scopes:

```sh
make eval RUNS=runs        # every table under runs/, e.g. a rehearsal table
make eval SEAT=6           # one seat only
uv run python -m evals --runs runs --team wolfpack   # by game.json team label
uv run python -m evals --runs runs --latest 5        # only the 5 most recent games
uv run python -m evals.offline --fake                # harness self-test, no LLM
```

Then:

1. `make check`, `make practice N=5` and `make eval`. Read the red gates and `bot.log`.
2. Fill in `HANDOFF.md` and hand it to your coding agent.
3. Review what the agent changed. It cannot edit `evals/`, and that's the point.

Add your own puzzles as `evals/puzzles/*.json` by hand if you want more coverage.

## Negative control

`tests/fixtures/runs/` includes an always-vote-first-seat bot's logs. It must fail
vote accuracy. If your suite passes it, your suite is dead.
