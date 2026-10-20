# Define Finished — organiser repo

Code for the "Define Finished" onsite (Wed 21 Oct 2026): teams build an LLM Werewolf bot and prove it with
evals they write themselves. Everything runs on laptops; there is no hosted infra.
`docs/spec.md` is the source of truth for shared contracts — read the relevant section before changing one.

## Layout

| Path | What |
|---|---|
| `werewolf/engine/` | Pure rules engine: no I/O, no clock, seeded RNG |
| `werewolf/server/` | MCP server, board, spectator SSE, LLM proxy, budget, finale, `ww` CLI |
| `werewolf/housebots/` | Rule-based house bots: `random`, `quiet`, `accuser`, `baseline`, plus the `forger` expansion (no LLM calls) |
| `connect4/` | Part 1 exercise: engine, solver, puzzles, naive bot, eval harness |
| `alibi/` | "The Agent's Alibi" transcript-audit exercise: fixtures, bots, harness, scorer checks |
| `alibi/migrate/`, `alibi/zig/` | Part 1 migration: port check-alibi to Zig; golden outputs, parity checks, team stubs |
| `reference/alibi/` | Organiser-only reference scorers, check notes, and a reference Zig port plus fake port |
| `starter/` | The kit teams receive; has its own `CLAUDE.md`, `pyproject.toml` and `uv.lock` |
| `dogfood/` | `team_a/`, `team_b/`: starter-kit copies played through as rehearsal teams |
| `prices.toml` | Model prices, house-bot and power-up prices, penalties; the server only accepts models listed here |

## Commands

```sh
uv run pytest -q                       # werewolf, connect4, alibi, reference
make test-c4 | make eval-c4            # Connect Four
make test-alibi | make check-alibi | make eval-alibi
ALIBI_HELDOUT_SEED=<seed> make reveal-alibi ALIBI_BOT=<bot>
make setup | make zig | make golden | make parity | make parity-{parse,score,report}
ALIBI_HELDOUT_SEED=<seed> make reveal-parity [ALIBI_ZIG_BIN=<binary>]
make fake-port                          # organiser: lookup-table port that fails reveal-parity
```

A local game with zero model spend:

```sh
uv run ww serve --port 8040 --fake-llm --runs-dir <scratch>/runs --quiet
uv run ww table --server http://127.0.0.1:8040 --runs-dir <scratch>/runs \
  --bots baseline,baseline,accuser,baseline,quiet,baseline,random
# spectator: http://127.0.0.1:8040/?op=<operator token from <scratch>/runs/tokens.json>
```

For a real LLM seat, drop `--fake-llm`, `source scripts/bifrost-env.sh` before `ww serve`, seat six house bots,
and run `uv run bot` in `starter/` with `WW_SERVER` and `WW_TOKEN` (seat 6's token). That spends real money.
Port 8000 is often taken on this laptop; pick another.

## Rules

- **Locked evals.** `.claude/hooks/` blocks agent edits to `connect4/team_evals/` and to `alibi/team_evals/`,
  `alibi/datasets/`, `reference/alibi/`, `alibi/golden/`, `alibi/migrate/`, `alibi/checks.py` and `alibi/scorers.py`
  while their `.locked` file exists. Never route around a hook, and never run `make unlock-*`; a human unlocks.
- **Zig is pinned** at 0.16.0 (`make setup` or `make zig`, into ignored `.tools/`); 0.15.x cannot link on macOS 26.
- **Teams write their own evals.** The starter ships a harness, not a suite. Don't add scorers, gates or datasets
  to `starter/evals/` or the `team_evals/` dirs; example scorers go in scratch space, not the repo.
- **Keys.** In a game, a bot never holds a Bifrost key; it calls the server's `/llm/v1/chat/completions` proxy with a seat token.
  The server reads per-team key env var *names* from `teams.toml` (local, untracked; see `teams.example.toml`).
  The offline `make check` is the exception: it calls Bifrost directly with the team's own
  `BIFROST_API_KEY`/`BIFROST_BASE_URL` (`starter/bot/puzzle_decide.py`) and spends it. Never print a key value or read `.env`.
- **Held-out data stays out of git.** `alibi/datasets/heldout.jsonl` is ignored; held-out rows come from `ALIBI_HELDOUT_SEED`.
- **Game output.** `runs/` is ignored. Point throwaway games at a scratch `--runs-dir`.
- Evals run on Braintrust `Eval()` and score locally by default; `BT_SEND=1` only controls Braintrust logging.
  A real `make check` still sends prompts to Bifrost directly and spends the team's key (see Keys).
