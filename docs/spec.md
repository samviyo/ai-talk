# Define Finished: build spec

Source of truth for the shared contracts. Talk content lives in
`~/Klaviyo/Repos/using-ai-talk-2hr.md`; this file only fixes what the code must agree on.

## Topology

Everything runs on laptops. No hosted infra.

- **Practice:** each team runs `ww serve` locally plus house bots and its own bot.
- **Finale:** teams push their bot to a branch; Sam's laptop runs `ww serve --final`,
  launches every team bot as a subprocess, and projects the spectator view.
- **LLM calls:** bots never hold a Bifrost key. They call the game server's
  OpenAI-compatible proxy (`POST /llm/v1/chat/completions`) with their seat token. The
  server forwards to Bifrost dev with that team's virtual key, meters cost, and enforces
  the in-game budget. Bifrost's own per-key cap is only the real-money backstop.

## Layout (each unit owns its path)

| Path | Unit |
|---|---|
| `werewolf/engine/` | Pure rules engine. No I/O, no clock, seeded RNG |
| `werewolf/server/` | MCP server, board, spectator SSE, LLM proxy, budget, table runner, CLI `ww` |
| `werewolf/housebots/` | random, quiet, accuser, baseline |
| `connect4/` | Part 1: engine, solver, puzzles, naive bot, eval harness |
| `starter/` | Team kit: bot template, werewolf eval harness, `evals/` lock hook |

Python 3.12, `uv`, one workspace `pyproject.toml` at the root. Tests with `pytest`.

## Engine contract (`werewolf/engine`)

```python
Role = Literal["werewolf", "seer", "doctor", "villager"]
Phase = Literal["night", "day_discussion", "day_vote", "over"]

@dataclass(frozen=True)
class Config:
    seats: int                 # 7 practice, 10-12 final
    wolves: int                # 2 practice, 3 final
    discussion_rounds: int     # 2 practice, 1 final
    seed: int

class Game:
    def __init__(self, config: Config): ...
    phase: Phase; day: int; round: int          # round = discussion round within the day
    def role(self, seat: int) -> Role: ...
    def alive(self) -> list[int]: ...
    def pending(self) -> dict[int, list[str]]:  # seat -> allowed action kinds right now
    def act(self, seat: int, action: Action) -> None  # raises IllegalAction(code, msg)
    def abstain(self, seat: int) -> None        # used by the clock and by broke seats
    def events(self, viewer: int | None) -> list[Event]  # viewer=None is omniscient
    winner: Literal["village", "wolves"] | None
```

- Actions: `NightKill(target)`, `SeerCheck(target)`, `DoctorSave(target)`,
  `Post(chat, claimed_role=None, accuse=None)`, `Vote(target)`, `WolfChat(msg)`.
- The phase advances when every seat in `pending()` has acted or abstained.
- Night: wolves must agree. The kill is the target with the most wolf votes; ties are
  broken by the seeded RNG. Doctor may save self, not the same seat two nights running.
- Vote: plurality is eliminated; a tie eliminates nobody. The role is revealed on death.
- Win: no wolves alive → village; wolves ≥ non-wolves → wolves.
- `events(viewer)` must never leak a role the viewer could not know. This is tested by
  scanning every event a non-omniscient viewer sees across 100 seeded games.
- Error codes: `not_your_turn`, `invalid_target`, `dead_seat`, `bad_schema`, `too_long`
  (chat > 280 chars).

## MCP tools (`werewolf/server`, streamable HTTP at `/mcp`)

Auth: `Authorization: Bearer <seat token>`. Each token maps to (table, seat, team).

| Tool | Returns |
|---|---|
| `wait_for_turn(timeout_s=25)` | `{phase, day, round, you_may:[...], announcements:[...], deadline_s}` or `{status:"waiting"}` on timeout. The only moderator channel |
| `get_my_role()` | `{role, partners:[seat]}` |
| `read_board(since=0)` | `{posts:[{id, seat, day, round, chat, claimed_role, accuse}], next}` |
| `post(chat, claimed_role?, accuse?)` | `{ok}` |
| `vote(seat)` | `{ok}` |
| `night_action(target)` | `{ok}` (kill, check or save by role; a seer check also returns `{role}`) |
| `wolf_chat(msg)` / `read_wolf_chat(since=0)` | `{ok}` / `{messages, next}` |
| `get_budget()` | `{spent_usd, limit_usd, remaining_usd, broke}` |

Server rules:
- A budget of 10 tool calls per seat per phase. Past that, calls return `call_budget_exceeded`.
- Phase clock: 20s per action, enforced lazily on every request and by a 1s ticker task.
  A missed deadline means the seat abstains.
- Every tool call is appended to `runs/<table>/trace.jsonl`:
  `{ts, seat, tool, args, result_code, latency_ms}`.
- Board content is returned as data only; announcements only come from `wait_for_turn`.

## LLM proxy and budget

- `POST /llm/v1/chat/completions`, OpenAI-compatible, bearer = seat token.
- Forwards to `${BIFROST_BASE_URL}/v1/chat/completions` with the team's virtual key from
  `teams.toml`. `BIFROST_BASE_URL` comes from the environment, no default to prod.
- Cost = usage tokens × price from `prices.toml` (per model, USD per 1M in/out tokens).
- An in-flight call completes. Once `spent ≥ limit` the seat is broke: proxy returns 402,
  `post` returns `broke`, and the server rolls the seat's actions randomly. A funded wolf
  still picks the pack's kill.
- `teams.toml`: `[teams.<name>] bifrost_key_env = "..."`. Keys live in env vars, never in
  files committed to git.
- `--fake-llm` mode returns canned completions with synthetic usage, for tests and dry
  runs without network.

## Spectator

- `GET /` serves one static HTML page; `GET /events` is SSE of omniscient events plus
  per-seat spend and broke state. Roles are shown. Broke seats grey out.

## CLI

- `ww serve [--seats 7] [--final] [--fake-llm] [--tables N]`
- `ww table --bots random,quiet,accuser,baseline,...` runs house bots against a server.
- `ww tokens` prints seat tokens for a table.

## Connect Four (`connect4/`)

- Engine (7×6), a perfect-ish solver (negamax with alpha-beta, depth-limited is fine for
  puzzle generation), puzzle sets `win_in_1` and `block_in_1` generated from the solver.
- `bots/naive.py`: one LLM call per move through Bifrost (direct, env
  `BIFROST_BASE_URL` + `BIFROST_API_KEY`, OpenAI-compatible), prompt-only.
- `make eval` runs the checks in the talk doc and prints a scoreboard, including the
  always-column-0 negative control.
- `tournament.py`: round-robin between bot modules.

## Starter kit (`starter/`)

- `bot/`: a minimal MCP-driven Werewolf agent that calls the proxy. It plays legally
  but badly.
- `evals/`: the team-facing checks from the talk doc, reading traces and engine logs.
- `.claude/settings.json` with a PreToolUse hook that blocks edits under `evals/`.
- `make eval`, `make practice` (spins up a local server and house bots).
