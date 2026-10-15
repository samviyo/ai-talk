# Define Finished: build spec

Source of truth for the shared contracts. Talk content lives in
`~/Klaviyo/Repos/using-ai-talk-2hr.md`; this file only fixes what the code must agree on.

## Topology

Everything runs on laptops. No hosted infra.

- **Practice:** each team runs `ww serve` locally plus house bots and its own bot.
- **Finale:** teams push their bot to a branch; Sam's laptop runs `ww final final.toml`,
  launches every team bot as a subprocess, and projects the spectator view.
- **LLM calls:** bots never hold a Bifrost key. They call the game server's
  OpenAI-compatible proxy (`POST /llm/v1/chat/completions`) with their seat token. The
  server forwards to Bifrost dev with that team's virtual key, meters cost, and enforces
  the in-game budget. Bifrost's own per-key cap is only the real-money backstop.

## Layout (each unit owns its path)

| Path | Unit |
|---|---|
| `werewolf/engine/` | Pure rules engine. No I/O, no clock, seeded RNG |
| `werewolf/server/` | MCP server, board, spectator SSE, LLM proxy, budget, table runner, finale launcher, CLI `ww` |
| `werewolf/housebots/` | random, quiet, accuser, baseline |
| `connect4/` | Part 1: engine, solver, puzzles, naive bot, eval harness |
| `starter/` | Team kit: bot template, werewolf eval harness, `evals/` lock hook |

Python 3.12, `uv`, one workspace `pyproject.toml` at the root. Tests with `pytest`.

## Roles and formats

Exactly 1 seer, 1 doctor, `wolves` werewolves, the rest villagers. `Config` validation
rejects `seats < 2 * wolves + 3`. Roles are assigned by a shuffle from `seed`.

| Format | Seats | Wolves | Discussion rounds | Max days |
|---|---|---|---|---|
| practice | 7 | 2 | 2 | 5 |
| final | 10–12 | 3 | 2 | 6 |

## Engine contract (`werewolf/engine`)

```python
Role = Literal["werewolf", "seer", "doctor", "villager"]
Phase = Literal["night", "day_discussion", "day_vote", "over"]

@dataclass(frozen=True)
class Config:
    seats: int
    wolves: int
    discussion_rounds: int
    max_days: int
    seed: int

class Game:
    def __init__(self, config: Config): ...
    phase: Phase; day: int; round: int
    turn_id: str                                 # changes every time a window opens
    def role(self, seat: int) -> Role: ...
    def alive(self) -> list[int]: ...
    def pending(self) -> dict[int, str]:          # seat -> the one obligation it owes now
    def act(self, seat: int, action: Action) -> None   # raises IllegalAction(code, msg)
    def abstain(self, seat: int) -> None
    def wolf_chat(self, seat: int, msg: str) -> None   # optional; never clears an obligation
    def legal_targets(self, seat: int) -> list[int]
    def events(self, viewer: int | None) -> list[Event]  # viewer=None is omniscient
    result: Literal["village", "wolves", "draw"] | None
```

**Windows.** The game is a sequence of windows: each night, each discussion round, each
vote. A window opens for every seat in `pending()` at once and closes when all of them
have acted or abstained. `turn_id` identifies the open window.

**Obligations.** `pending()` maps a seat to exactly one obligation: `night_kill`,
`seer_check`, `doctor_save`, `post` or `vote`. Villagers owe nothing at night. One
accepted action or one abstention clears it; a second action returns `already_acted`.

**Night.** Each living wolf submits one `NightKill`. The kill is the plurality target among
submitted ballots; a tie is broken by the seeded RNG; zero ballots means no kill. The
doctor may save itself, but not the same seat two nights running. A saved target lives.

**Discussion.** Posts made during a round are held privately and revealed together when
the window closes, so posting early never costs information.

**Vote.** Ballots are private until resolution. Plurality is eliminated; a tie or zero
ballots eliminates nobody. The role is revealed on death.

**End.** No wolves alive → `village`. Wolves ≥ non-wolves → `wolves`. If day `max_days`
ends with neither, the result is `draw`.

**Visibility.** `events(viewer)` returns only what that seat may know: its own role, wolf
partners and wolf chat for wolves, its own seer results, public posts after reveal, deaths
with revealed roles, and vote tallies after resolution.

Error codes: `not_your_turn`, `already_acted`, `stale_turn`, `invalid_target`,
`dead_seat`, `bad_schema`, `too_long` (chat > 280 chars, wolf chat > 500).

## MCP tools (`werewolf/server`, streamable HTTP at `/mcp`)

Auth: `Authorization: Bearer <seat token>`. A seat token maps to (table, game, seat, team)
and authorizes only that seat's view. The server serializes every mutation, clock check
and phase advance per table under one lock.

| Tool | Returns |
|---|---|
| `wait_for_turn(timeout_s=25)` | Server caps the wait at 3.5s (httpx's default 5s read timeout). Each (seat, window) is served once; a re-poll blocks until the next window. Returns `{turn_id, phase, day, round, you_owe, legal_targets, announcements, deadline_s}`, or `{status:"waiting"}` on timeout, or `{status:"over", result}`. Between games (auto-restart on) it returns `{status:"idle", result}` after each seat has heard `over` once; the next game opens frozen until every seat calls `get_my_role`. Wakes immediately when a window opens for this seat. The only moderator channel |
| `get_my_role()` | `{seat, role, partners}`. `partners` is empty unless werewolf |
| `read_board(since=0)` | `{posts:[{id, seat, day, round, chat, claimed_role, accuse}], next}`. Revealed posts only |
| `post(turn_id, chat, claimed_role?, accuse?)` | `{ok}` |
| `vote(turn_id, seat)` | `{ok}` |
| `night_action(turn_id, target)` | `{ok}`; a seer check also returns `{role}` |
| `wolf_chat(msg)` / `read_wolf_chat(since=0)` | Werewolves only; others get `not_your_turn`. Read returns `{messages:[{id, seat, day, msg}], next}` |
| `get_budget()` | `{spent_usd, limit_usd, remaining_usd, broke}` |

Announcements are structured dicts, never free text: `{kind: "death", seat, role, cause:
"night"|"vote"}`, `{kind: "no_death", cause}`, `{kind: "seer_result", seat, role}` (to the
seer only), `{kind: "phase", phase, day, round}`, `{kind: "result", result}`.

MCP SDK: `mcp>=2.3,<3` everywhere. Clients pass bearer auth through
`httpx.AsyncClient(headers=...)` to `streamable_http_client(url, http_client=...)`, which
yields `(read, write)`; results use snake_case (`structured_content`, `is_error`).

Server rules:
- Mutations need the current `turn_id`; an old one returns `stale_turn`. The exposed
  `turn_id` is game-scoped (`g<game>:<phase>:<day>:<round>`), so an id from a previous
  game at the same table is always stale.
- A budget of 10 tool calls per seat per window. Past that, calls return
  `call_budget_exceeded`.
- **Clock:** each window's deadline is 20s after it opens (30s in the final), measured on
  the monotonic clock. A 1s ticker and every request check it under the table lock. An
  action that arrives after the deadline is rejected with `stale_turn`. Reading and
  waiting never extend a deadline.
- **Pacing:** `--min-window-s` holds each window's release until at least that long after
  the previous window opened, and `--transition-s` holds it further when the phase
  crosses night↔day. The window's deadline is `release + window_s`; while held,
  `wait_for_turn` returns `{status:"waiting"}` and no fallback can fire. The spectator
  tick reports `held_s` (seconds until release) and `transition` (true during a
  phase-change hold); `remaining_s` stays at the full `window_s` while held.
- Missed deadline: a funded seat abstains. A broke or dead-process seat gets the
  fallback below.
- The leak test scans every MCP response, including errors and announcements, for every
  seat across 100 seeded games and asserts no role is visible that the seat may not know.
- Traces: every tool call is buffered as `{ts, seat, tool, args, result_code, latency_ms}`
  and written to `runs/<table>/<game>/trace.jsonl` when the game ends, never while it runs.

## Budget and fallback

- Allowance: `--budget-usd` per seat per game (default 0.50, set properly after the dry
  run). The ledger key is (table, game, seat); it resets each game.
- House bots use a separate house key and are not metered against the game.
- **Broke** means `spent ≥ limit`. From then the proxy returns 402 and `post` returns
  `broke`. A seat whose bot process has exited is treated the same way.
- **Fallback at the deadline** for a broke seat: vote, seer check and doctor save are
  random legal targets; no post. A broke wolf submits no kill ballot while any funded wolf
  is alive; if every living wolf is broke, one random legal kill is submitted.

## LLM proxy

- `POST /llm/v1/chat/completions`, OpenAI-compatible subset, bearer = seat token.
- Only models listed in `prices.toml` are allowed (`model_not_allowed` otherwise).
  `stream=true` is rejected. `max_tokens` is required and capped at 2048.
- One in-flight call per seat; a second concurrent call returns 409. So the only
  overshoot is one call, bounded by `max_tokens`.
- Forwards to `${BIFROST_BASE_URL}/v1/chat/completions` with the team's virtual key.
  `BIFROST_BASE_URL` comes from the environment, with no default to prod.
- Charge = usage × price. If usage is missing, charge input as chars/4 plus `max_tokens`
  of output. If the upstream call errors or times out (60s), charge the input estimate.
  Nothing is ever free by accident.
- Upstream auth sends the key as both `Authorization: Bearer` and `x-bf-vk`. The developer
  gateway (`https://developer-gateway.services.clovesoftware-dev.com`) accepts
  `/v1/chat/completions` with a personal key and returns OpenAI usage, so dogfooding can use
  it before team keys exist. Seed `prices.toml` from the gateway's published model costs.
- `teams.toml`: `[teams.<name>] bifrost_key_env = "..."`. Keys live in env vars of the
  server process only.
- `--fake-llm` mode returns canned completions with synthetic usage, for tests and dry
  runs without network.

## Spectator

- `GET /` serves one static HTML page; `GET /events` is SSE of omniscient events plus
  per-seat spend and broke state. Roles are shown. Broke seats grey out.
- Both need the operator token (`?op=<token>`, printed at startup). Seat tokens are
  refused.

## Finale (`ww final final.toml`)

```toml
seats = 12
seed = 2026
[[teams]]
name = "wolfpack"
repo = "git@github.com:...#<commit>"   # pinned commit
cmd = "uv run bot"                     # run inside the checkout
```

- Each team gets exactly one seat; baseline house bots fill the rest. Seats are
  shuffled with the seed.
- Each bot gets only `WW_SERVER`, `WW_TOKEN`, `PATH` and `HOME`. No other env is
  inherited, so no Bifrost key can leak to team code. `uv sync` during install also
  runs with a sanitized env (PATH, HOME, and UV_* cache settings only).
- Per-team Bifrost keys come from `teams.toml` (same file as `ww serve`); the finale
  refuses to start if any assigned team's key env var is unset.
- Bots run in their own process groups; shutdown and replacement kill the group.
- **Readiness barrier:** the first window stays closed until every seat has called
  `get_my_role`, or after 90s. A seat that is not ready by then is replaced by a
  baseline house bot. The clock starts only when the barrier opens.
- A bot process that exits mid-game is treated as broke for the rest of the game.
- `ww final --check final.toml` clones, installs and smoke-starts every bot, then exits.

## CLI

- `ww serve [--format practice|final] [--seats N] [--fake-llm] [--tables N] [--budget-usd X] [--min-window-s S] [--transition-s S]`
- `ww table --bots random,quiet,accuser,baseline,...` runs house bots against a server.
- `ww tokens` prints seat tokens for a table.
- `ww final [--check] final.toml [--min-window-s S] [--transition-s S]` (pacing defaults: min 10s, transition 8s)

## Connect Four (`connect4/`)

- Engine (7×6), a solver (negamax with alpha-beta; depth-limited is fine), puzzle sets
  `win_in_1` and `block_in_1` generated from the solver.
- `bots/naive.py`: one LLM call per move through Bifrost (direct, env
  `BIFROST_BASE_URL` + `BIFROST_API_KEY`, OpenAI-compatible), prompt-only.
- `make eval-c4` runs the checks in the talk doc and prints a scoreboard, including the
  always-column-0 negative control.
- `tournament.py`: round-robin between bot modules.

## Starter kit (`starter/`)

- `bot/`: a minimal MCP-driven Werewolf agent that calls the proxy. It plays legally
  but badly. Started with `uv run bot`, reading `WW_SERVER` and `WW_TOKEN`.
- `evals/`: the team-facing checks from the talk doc, reading traces and engine logs.
- `.claude/settings.json` with a PreToolUse hook that blocks edits under `evals/`.
- `make eval`, `make practice` (spins up a local server and house bots).
