# Define Finished: build spec

Source of truth for the shared contracts. Talk content lives in
`~/Klaviyo/Repos/using-ai-talk-2hr.md`; this file only fixes what the code must agree on.

## Topology

Everything runs on laptops. No hosted infra.

- **Practice:** each team runs `ww serve` locally plus house bots and its own bot.
- **Finale:** teams run their bot on their own laptop against the organiser's server
  (`ww final final.toml`, laptop seats by default); Sam's laptop projects the spectator
  view. A team can instead pin a repo commit the organiser runs.
- **LLM calls:** bots never hold a Bifrost key. They call the game server's
  OpenAI-compatible proxy (`POST /llm/v1/chat/completions`) with their seat token. The
  server forwards to Bifrost dev with that team's virtual key, meters cost, and enforces
  the in-game budget. Bifrost's own per-key cap is only the real-money backstop.

## Layout (each unit owns its path)

| Path | Unit |
|---|---|
| `werewolf/engine/` | Pure rules engine. No I/O, no clock, seeded RNG |
| `werewolf/server/` | MCP server, board, spectator SSE, LLM proxy, budget, table runner, finale launcher, CLI `ww` |
| `werewolf/housebots/` | random, quiet, accuser, baseline; forger (the expansion) |
| `connect4/` | Part 1: engine, solver, puzzles, naive bot, eval harness |
| `starter/` | Team kit: bot template, werewolf eval harness, `evals/` lock hook |

Python 3.12, `uv`. Two projects: the root `pyproject.toml` (engine, server, house bots,
connect4, alibi) and `starter/pyproject.toml` (the team kit, synced separately). Tests with `pytest`.

## Roles and formats

Exactly 1 seer, 1 doctor, `wolves` werewolves, the rest villagers. `Config` validation
rejects `seats < 2 * wolves + 3`. Roles are assigned by a shuffle from `seed`.

| Format | Seats | Wolves | Discussion | Max days |
|---|---|---|---|---|
| practice | 7 | 2 | 60s live chat, 3 posts each | 5 |
| final | 10–16 | `seats // 4`, min 3 | 40s live chat (+2.5s per seat past 12), 2 posts each | 6 (7 past 12 seats) |

## Engine contract (`werewolf/engine`)

```python
Role = Literal["werewolf", "seer", "doctor", "villager"]
Phase = Literal["night", "day_discussion", "defence", "day_vote", "reveal", "over"]

@dataclass(frozen=True)
class Config:
    seats: int
    wolves: int
    max_days: int
    seed: int
    discussion_s: float = 60.0
    max_posts: int = 3

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

**Windows.** The game is a sequence of windows: each night, each day's discussion, each
vote. Night and vote windows open for every seat in `pending()` at once and close when all
of them have acted or abstained, or at the deadline. The discussion window is timed: it
always runs its full length (`discussion_s`) and never closes early. `turn_id`
identifies the open window.

**Obligations.** `pending()` maps a seat to exactly one obligation: `night_kill`,
`seer_check`, `doctor_save`, `defence` or `vote`. Discussion owes nothing (a quiet seat
is not abstaining); a standing nominee owes `defence`. Villagers owe nothing at night. One
accepted action or one abstention clears it; a second action returns `already_acted`.

**Night.** Each living wolf submits one `NightKill`. The kill is the plurality target among
submitted ballots; a tie is broken by the seeded RNG; zero ballots means no kill. The
doctor may save itself, but not the same seat two nights running. A saved target lives.

**Discussion (live chat).** One discussion window per day, `discussion_s` long from its
release (practice 60s, final 40s), never closing early. Every living, funded seat may post
up to `max_posts` times (default 3; `extra_post` adds one). Accepted posts join the floor
queue; they become public only when the floor releases them (see "The floor"). `reply_to`
must name a public post of the current game. A seat must wait 5s between its own released
posts. No obligation is owed during discussion, so a quiet seat is not abstaining.
`discussion_rounds` is removed from `Config`; `discussion_s` and `max_posts` replace it.

**The floor.** One voice at a time. An accepted `post()` returns `{ok, queued: position}`
and joins a FIFO queue; the server releases one post every `--floor-s` (default 4). A post
is visible on `read_board`, to mentions and over SSE only once released; mention wakes fire
on release. Each seat may hold one unreleased post: a second returns `{"error":
"still_queued"}` and is not charged. When `discussion_s` ends the floor drains for up to
20s, then drops the rest: a public `{kind: "cut_off", seats}` event lands at window close
and the dropped posts are refunded. SSE gets `floor{speaking: seat|null, queue: [seats]}`
on every change. Wolf chat and seer channels are never queued.

**Right of reply.** A released post with `accuse`, `nominate` or `second` targeting X
grants X one reply: at most one grant per distinct accuser per day, and the grant replaces
the mention wake for that pair. X sees `you_owe: "reply"` with `{reply_to, from, chat}` on
`wait_for_turn` and `read_board`. The reply is a `post` with `reply_to`: it spends no post,
ignores the 5s gap, is allowed after `max_posts`, and jumps the floor queue behind the
current speaker. An unused grant expires quietly at day end and doesn't count toward the
silent list. SSE: `{kind: "reply_owed", seat, from, post_id}` to the accused seat.

**News and reactions.** `wait_for_turn` takes an optional `since: int` event cursor; when
a public event lands after it (a released post, reply, reaction, nomination, seconded,
standing, death or phase), it returns early with `{status: "news", events: [...max 10],
cursor}`. An owed reply or hot seat takes priority, with news attached. Without `since`,
behaviour is unchanged. `react(post_id, kind)` with `kind` in `agree|doubt|suspect` is
public immediately, never queued: SSE `{kind: "reaction", seat, post_id, target_seat,
reaction}`. Limits: once per seat per post, at most 8 per seat per day, only in
discussion/defence, never on your own post. Reacting counts as not silent and sits in the
10-call budget. `read_board` posts carry `reactions: {agree, doubt, suspect}` counts.
`suspect` does not grant a reply.

**Hot seat.** After `--hot-seat-s` (default 6) of dead air in `day_discussion` (nobody
speaking or queued), the server picks the living seat with the fewest posts today, ties
broken by the seeded RNG; no seat repeats until all have been hot that day, and a seat
with a queued post is never picked. It gets `you_owe: "hot_seat"` with `{prompt}` from a
small fixed seeded list, and must land a `post()` within `--hot-seat-answer-s` (default 5)
on the server clock. An in-time answer jumps the queue and spends no post. A miss charges
`hot_seat_fine` × the seat budget (prices.toml `[penalties]`, default 0.10) through the
same ledger, so it can trigger `broke`; a broke seat just passes. SSE `{kind: "hot_seat",
seat, prompt, deadline_s, fine_usd}`; a pass is `{kind: "hot_seat_pass", seat, fine_usd}`,
public on the board and news at window close, and the seat stays silent. A late answer is
a normal post: it counts toward `max_posts` and the fine still applies. The dead-air timer
restarts after an answer or a pass; never during the drain; one at a time.

**Making bots talk.** Three game rules push conversation:
- **Mentions wake you.** A post that names a living seat ("seat 7", "@7") or replies to
  that seat's post queues a `{kind: "mention", from, post_id, chat}` for it. During
  discussion `wait_for_turn` returns as soon as a seat has new board activity or
  mentions, with `you_owe: "discuss"`, a `board_cursor` and the living seats (`alive`); it may be served any number
  of times per window (the "served once" rule applies to night and vote only). Mentions
  are batched per wake, and each other seat can wake a given seat at most once per day.
- **Nominate to vote.** A post may carry `nominate: <seat>` (once per day) and/or
  `second: <seat>` (once per day, not the seat's own nomination). Targets must be living.
  When discussion ends, the up-to-3 nominees with the most distinct seconders stand, ties
  broken by the seeded RNG, so early nominations can be displaced by later support.
  Each standing nominee then gets a `defence` window of 15s from release: only that seat
  may post, exactly once, outside its `max_posts` and the 5s gap. A broke, exited or
  silent nominee's defence simply times out. Each window has its own `turn_id`
  (`...:defence:<seat>`). The vote then accepts only standing nominees.
  If no nominee stands, the vote is skipped: the engine emits `vote_skipped` with the
  silence list, nobody dies, and end-of-day processing (including the `max_days` draw)
  runs normally. Fallback votes sample only from standing nominees; with none there is
  no fallback ballot. A random fallback ballot keeps a purchased `double_vote` weight.
- **Silence is public.** The day's `vote_tally` (or `vote_skipped`) event carries
  `silent: [seats]`, the living seats that made no post and no reaction that day.
  `vote_tally` is spectator/log only; bots get the `silent` list via `vote_skipped`,
  and the day_vote announcement itself carries no silence list.

**Discussion state for bots.** `read_board(since)` returns posts plus
`nominations: [{target, by, seconds: [seats]}]`, `standing` (after close),
`discussion_ends_in_s` and `posts_left`. Defence ownership arrives as
`{kind: "phase", phase: "defence", seat}`.

**Call budget during discussion.** `wait_for_turn` and `read_board` don't count against
the 10-call budget; `read_board` is rate-limited instead to 1 call per second per seat
(excess returns `rate_limited`). `wait_for_turn` long-polls and is exempt from both the
budget and the rate limit, so a re-poll is never refused. `get_my_role` is exempt from
the budget too (it is how a seat joins and readies). The 10-call budget covers mutations
and `shop`/`buy`.

**Bot behaviour.** Discussion needs a loop, not one action, so the starter bot and house
bots get one: post an opener early, keep one post in reserve for replies, answer a
mention only when posts and time remain, nominate the most-suspected seat (on an empty
board: a seat-seeded pick among living non-partners), second when they agree, and bound
every LLM call by the time left in the window. The starter posts its opener as soon as
it can; the staggering (a seat-seeded 0–20s opener delay) is house-bot behaviour. A bot
bounds its own proxy call to the window's remaining time (the starter's hot seat and
news reactions use fixed 4s and 3s timeouts instead); the server's own upstream
timeout is a fixed 60s. Bots pass `since` to `wait_for_turn` and answer the free reply
and the hot seat; on `news` the starter makes at most one LLM call per batch to react or
ignore (reactions are cheap, a reply to an accusation is free, the hot seat is a 5s
speed test with low `max_tokens`). House brains: the accuser reacts `suspect` to its
target's posts and `agree` to accusations of them; the baseline agrees with accusations
of its top suspect and doubts their defences; the quiet brain rarely reacts and misses
about half its hot seats; random is random. All answer replies. House posts, votes,
night actions and wolf chat keep their simulated per-action cost; house reactions are
not charged.

Engine: `Phase` gains `defence`; `Post` gains `nominate`, `second`, `reply_to`;
`legal_targets` for `vote` returns standing nominees. Public events: `nomination{seat,
target}`, `seconded{seat, target}`, `standing{targets}`, `defence{seat}`,
`vote_skipped{silent}`.

**Vote.** Ballots are private until resolution. Plurality is eliminated; a tie or zero
ballots eliminates nobody. The role is revealed on death. Ballots carry `weight` (2 with
a purchased `double_vote`).

Implementation decisions where this spec was silent:
- Standing nominees are sorted ascending by seat; defences run in that order.
- Nomination ranking: most distinct seconders, ties broken by the seeded RNG.
- A vote fallback ballot is drawn only from standing nominees; with none, no ballot.
- `read_board` is rate-limited to 1/s per seat and exempt from the 10-call budget;
  `wait_for_turn` is exempt from both (it long-polls); excess `read_board` returns
  `rate_limited`.
- The server caps night/vote windows at `--window-s` (default 20s); discussion and
  defence use the engine's own `discussion_s` / 15s.
- `seer_result` is always deferred to night resolution (frames or not), so timing
  reveals nothing.
- `powerup` events are audience {buyer}; `whisper` events are audience {buyer, target};
  both appear in the omniscient spectator feed.
- Omniscient-only beats: `night_acted{seat}` on each accepted night action,
  `night_abstain{seat,role}` on a clock-defaulted night obligation, and
  `ballot_cast{seat}` on each accepted vote. Targets stay hidden until the staged
  reveal. No seat ever receives them.
- **Staged reveals.** The vote closes, holds for `--reveal-s` (default 1.5) per cast
  ballot, and emits one `ballot_reveal{seat,target,weight,tally}` in seeded-random order
  before `vote_tally` and any death. Night resolves into a `reveal` phase that emits
  `night_beat{step:"wolves",target}`, `{step:"doctor",target}` and
  `{step:"seer",seer,target,role}` about `--beat-s` (default 2.5) apart, skipping a step
  whose role abstained or is dead, before dawn. Both holds show in the spectator tick as
  `held_s>0`; `wait_for_turn` keeps returning `{status:"waiting"}` while held.

**End.** No wolves alive → `village`. Wolves ≥ non-wolves → `wolves`. If day `max_days`
ends with neither, the result is `draw`.

**Visibility.** `events(viewer)` returns only what that seat may know: its own role, wolf
partners and wolf chat for wolves, its own seer results, public posts as soon as they are accepted, deaths
with revealed roles, and vote tallies after resolution.

Error codes: `not_your_turn`, `already_acted`, `stale_turn`, `invalid_target`,
`dead_seat`, `bad_schema`, `too_long` (chat > 280 chars, wolf chat > 500).

## MCP tools (`werewolf/server`, streamable HTTP at `/mcp`)

Auth: `Authorization: Bearer <seat token>`. A seat token maps to (table, game, seat, team)
and authorizes only that seat's view. The server serializes every mutation, clock check
and phase advance per table under one lock.

| Tool | Returns |
|---|---|
| `wait_for_turn(timeout_s=25, since?)` | Server caps the wait at 3.5s (httpx's default 5s read timeout). Each (seat, window) is served once in night and vote; a re-poll blocks until the next window. In discussion a seat may be served again on new board activity, a mention, a reply or the hot seat. Returns `{turn_id, phase, day, round, you_owe, legal_targets, announcements, deadline_s}`, or `{status:"waiting"}` on timeout, or `{status:"over", result}`. With `since`, returns `{status:"news", events, cursor}` early when a public event lands after the cursor. Between games (auto-restart on) it returns `{status:"idle", result}` after each seat has heard `over` once; the next game opens frozen until every seat calls `get_my_role`. Wakes immediately when a window opens for this seat. The only moderator channel |
| `get_my_role()` | `{seat, team, role, partners}`. `partners` is empty unless werewolf |
| `read_board(since=0)` | `{posts:[{id, seat, day, chat, claimed_role, accuse, reply_to, reactions}], next, discussion_ends_in_s}`. Posts are visible once the floor releases them |
| `post(turn_id, chat, claimed_role?, accuse?, reply_to?, nominate?, second?, reason?)` | Discussion: `{ok, queued}` (queue position); a defence window: `{ok, posts_left}`. `still_queued` while the seat holds an unreleased post, `no_posts_left` past `max_posts` |
| `react(post_id, kind)` | `{ok}`; `kind` in `agree|doubt|suspect`, public immediately, once per seat per post, 8 per seat per day, never on your own post |
| `vote(turn_id, seat, reason?)` | `{ok}` |
| `night_action(turn_id, target, reason?)` | `{ok}`. A seer's result arrives as the `seer_result` announcement when night resolves (see Power-ups) |
| `wolf_chat(msg)` / `read_wolf_chat(since=0)` | Werewolves only; others get `not_your_turn`. Read returns `{messages:[{id, seat, day, msg}], next}` |
| `get_budget()` | `{spent_usd, limit_usd, remaining_usd, broke}` |
| `shop()` | `{items: [{item, price_usd, available, reason?}], remaining_usd}` |
| `buy(turn_id, item, target?, msg?, reason?)` | `{ok, remaining_usd}`; `insufficient_budget`, `already_bought`, `not_available`, `invalid_target`, `too_long` |

`reason` is the hole cam: audience-only, ≤200 chars, never in any seat response.

Announcements are structured dicts, never free text: `{kind: "death", seat, role, cause:
"night"|"vote"}`, `{kind: "no_death", cause}`, `{kind: "seer_result", seat, role}` (to the
seer only), `{kind: "phase", phase, day, round}`, `{kind: "result", result}`.

MCP SDK: `mcp>=2.3,<3` everywhere. Clients pass bearer auth through
`httpx.AsyncClient(headers=...)` to `streamable_http_client(url, http_client=...)`, which
yields `(read, write)`; results use snake_case (`structured_content`, `is_error`).

Server rules:
- Mutations need the current `turn_id`; an old one returns `stale_turn`. The exposed
  `turn_id` is game-scoped (`g<game>:<phase>:<day>:<round>`), so an id from a previous
  game at the same table is always stale. The raw engine id (without the `g<game>`
  prefix) is also accepted, but only while it names the current window, so it offers no
  extra reach.
- A budget of 10 tool calls per seat per window. Past that, calls return
  `call_budget_exceeded`.
- **Clock:** night and vote windows close `--window-s` (default 20s) after release;
  discussion runs `discussion_s` and each defence 15s, all measured on the monotonic
  clock. A 1s ticker and every request check it under the table lock. An
  action that arrives after the deadline is rejected with `stale_turn`. Reading and
  waiting never extend a deadline.
- **Pacing:** `--min-window-s` holds each window's release until at least that long after
  the previous window opened, and `--transition-s` holds it further when the phase
  crosses night↔day. The window's deadline is `release + window_s`; while held,
  `wait_for_turn` returns `{status:"waiting"}` and no fallback can fire. The spectator
  tick reports `held_s` (seconds until release) and `transition` (true during a
  phase-change hold); `remaining_s` stays at the full `window_s` while held. The tick
  also carries `lobby: true` while a manual-start table waits for `/admin/start`.
- Missed deadline: a funded seat abstains. A broke or dead-process seat gets the
  fallback below.
- The leak test scans every MCP response, including errors and announcements, for every
  seat across 20 seeded games and asserts no role is visible that the seat may not know.
- Traces: every tool call except `wait_for_turn` is buffered as `{ts, seat, tool, args,
  result_code, latency_ms}` and written to `runs/<table>/<game>/trace.jsonl` when the
  game ends, never while it runs.

## Budget and fallback

- Allowance: `--budget-usd` per seat per game (default 0.10, set properly after the dry
  run). The ledger key is (table, game, seat); it resets each game.
- **House bots pay a simulated cost.** They make no LLM calls, so each accepted action
  (post, vote, night action, wolf chat, `buy` excluded) debits a simulated price from that
  seat's ledger. Prices per brain live in `prices.toml` under `[housebots]`, defaulting to
  random $0.0004, quiet $0.0008, accuser $0.0024, baseline $0.0016, roughly a Haiku-class call.
  House seats follow every budget rule: they can go broke, go mute and fall back to random.
- **Fines.** `prices.toml [penalties]` holds `hot_seat_fine` (default 0.10), the fraction
  of the seat budget charged when a seat misses the hot seat. Fines debit the same ledger,
  so a fine can flip a seat to broke; a post cut off in the floor drain is refunded.
- The server, never the client, decides which seats are house bots: `ww final` knows the
  seats it fills, and `ww table` registers its seats through `POST /admin/house` with the
  operator token. The spectator labels them `house · <brain>`.
- **Broke** means `spent ≥ limit`. From then the proxy returns 402 and `post` returns
  `broke`. A seat whose bot process has exited is treated the same way.
- **Fallback at the deadline** for a broke seat: vote, seer check and doctor save are
  random legal targets; no post. A broke wolf submits no kill ballot while any funded wolf
  is alive; if every living wolf is broke, one random legal kill is submitted.

## Power-ups

Agents can spend part of their in-game budget on power-ups. A purchase debits the same
per-seat ledger as LLM calls, so buying trades thinking for advantage. Prices live in
`prices.toml` under `[powerups]` so they can be tuned after the dry run. Every seat,
house bots included, buys from the same ledger (see Budget and fallback).

| Item | Default | Who | Buy during | Limit | Effect |
|---|---|---|---|---|---|
| `extra_post` | $0.01 | living seat | during discussion | 1 per day | one more post today (`max_posts` + 1) |
| `whisper` | $0.016 | living seat | any day window | 1 per day | private message (≤280 chars) to one other living seat, delivered when the window closes |
| `double_vote` | $0.03 | living seat | its `vote` window, any time before the vote resolves | 1 per game | its ballot counts twice |
| `shield` | $0.04 | living non-wolf | any day window except on day `max_days` | 1 per day | protects the buyer from the kill on the coming night only |
| `frame` | $0.03 | living wolf | a night window | 1 per night | any living non-wolf target reads as `werewolf` to the seer tonight |

**Purchase rules**
- Price must be ≤ remaining budget, else `insufficient_budget`. Over the limit:
  `already_bought`. Wrong time or role: `not_available`. A bad whisper or frame target is
  `invalid_target`; an empty whisper message is `bad_schema`; an over-long message is
  `too_long`. Rejected buys charge nothing. `shop()` returns the same availability, so
  errors reveal nothing it doesn't.
- No refunds. An effect whose buyer or target dies, or that never triggers, expires.
- A purchase that leaves the seat broke follows the broke rules: it gets no further
  manual actions (`broke`) and the deadline fallback acts for it. Purchased effects stay
  active, so a broke seat's random ballot still counts twice after `double_vote`.

**Resolution**
- Night, in order: frames apply, then the seer's check resolves, then the kill plurality,
  then the doctor save and any shield. A protected target lives and the night shows as
  `no_death` either way.
- With frames, a seer check no longer returns the role immediately: `night_action` returns
  `{ok}` and the result arrives as the `seer_result` announcement when night resolves.
  This holds for every check, framed or not, so timing reveals nothing.
- Votes: weighted totals; a tie or zero weight eliminates nobody. The tally lists each
  ballot with its `weight`.
- Whispers reach the target only through `read_board`, as a `whispers: [{from, msg}]`
  list; `wait_for_turn` does not deliver them. Board text and whispers are
  untrusted player content.

**What other seats can see.** Purchase records are private to the buyer. Others see only
these effects: one post more than the usual limit, a ballot weight of 2 in the tally, a whisper
addressed to them, and whatever they infer from a `no_death` or a seer claim. The
spectator sees every purchase as it happens. The leak test covers `powerup` and
`whisper` events, error codes, and announcement timing as well as roles.

**Interfaces**
- Engine: `Game.apply_powerup(seat, item, target=None, msg=None)` raises
  `IllegalAction(not_available | already_bought | invalid_target | too_long)`. Budget
  checks stay in the server. Events: `powerup{seat, item, target}` (audience: buyer) and
  `whisper{seat, target, msg}` (audience: buyer and target), both in the omniscient view.
- MCP: `shop()` → `{items: [{item, price_usd, available, reason?}], remaining_usd}`;
  `buy(turn_id, item, target?, msg?)` → `{ok, remaining_usd}`. Both count against the
  10-calls-per-window budget.
- `game.json` events gain `{kind:"powerup", seat, item, target, usd}` and
  `{kind:"whisper", seat, target, msg}`. `spend` stays the total per seat, LLM plus items.
  The staged beats are recorded too: `{kind:"ballot_cast"|"night_acted", seat, day}`,
  `{kind:"ballot_reveal", seat, target, weight, tally, day}` and
  `{kind:"night_beat", step, ..., day}`.

## LLM proxy

- `POST /llm/v1/chat/completions`, OpenAI-compatible subset, bearer = seat token.
- Only models listed in `prices.toml` are allowed (`model_not_allowed` otherwise).
  `stream=true` is rejected. `max_tokens` is required and capped at 2048.
- One in-flight call per seat; a second concurrent call returns 409. So the only
  overshoot is one call, bounded by `max_tokens`.
- Forwards to `${BIFROST_BASE_URL}/v1/chat/completions` with the team's virtual key.
  `BIFROST_BASE_URL` is the bare gateway origin (no `/v1`) from the environment, with
  no default to prod — the server, and only the server, owns the `/v1` suffix.
- Charge = usage × price. If usage is missing, charge input as chars/4 plus `max_tokens`
  of output. If the upstream call errors or times out (60s), charge the input estimate.
  Nothing is ever free by accident.
- Upstream auth sends the key as both `Authorization: Bearer` and `x-bf-vk`. The developer
  gateway (`https://developer-gateway.services.clovesoftware-dev.com`) accepts
  `/v1/chat/completions` with a personal key and returns OpenAI usage, so dogfooding can use
  it before team keys exist. `scripts/gateway-env.sh` exports that origin as
  `BIFROST_BASE_URL` (no `/v1`); direct callers like the connect4 bots append `/v1`
  themselves. Seed `prices.toml` from the gateway's published model costs.
- `teams.toml`: `[teams.<name>] bifrost_key_env = "..."`. Keys live in env vars of the
  server process only.
- `--fake-llm` mode returns canned completions with synthetic usage, for tests and dry
  runs without network.

## Spectator

- `GET /` serves one static HTML page; `GET /events` is SSE of omniscient events plus
  per-seat spend and broke state. Roles are shown. Broke seats grey out.
- Both need the operator token (`?op=<token>`, printed at startup). Seat tokens are
  refused.
- SSE shapes: `{kind:"init", ...}` once on connect; `{kind:"tick", game, remaining_s,
  held_s, transition, lobby, pending, thinking, waiting_ready, seats}` every second;
  `{kind:"phase", phase, day}` on each window change; `{kind:"lobby", seats}` on every
  readiness change; `{kind:"game_start", game}` and `{kind:"game_over", game, result,
  summary}` at the ends; and one message per engine event carrying the event's own kind
  (`{kind:"phase"|"death"|"vote_tally"|...}`), not a `kind:"event"` wrapper. The staged
  beats arrive top-level without day/round: `{kind:"ballot_cast", seat}`,
  `{kind:"ballot_reveal", seat, target, weight, tally}`, `{kind:"night_acted", seat}`,
  `{kind:"night_beat", step, ...}`, `{kind:"thought", seat, text}`.

## Spectating (audience only)

The hole cam makes the projector a spectator sport. It doesn't change what bots can see or
do, and it is never sent to a seat.

**Hole cam.** `post`, `vote`, `night_action` and `buy` accept an optional `reason`
(≤200 chars). It is never shown to a seat; the server emits it to the spectator as
`{kind: "thought", seat, text}` and appends it to `game.json` under `thoughts` for the
retro. House bots pass their brain's one-line `reason` (e.g. "most accused: seat 4").
The leak test asserts no `thought` or `reason` text reaches any seat.

**Lobby and operator start.** With `--manual-start` (default on for `ww final`, off for
practice) the table opens in a lobby: seats call `get_my_role` (which returns
`{seat, role: null, partners: [], lobby: true}` until the game starts), the spectator
gets `{kind: "lobby", seats: [{seat, team, house, ready}]}` on every readiness change,
and no window opens until `POST /admin/start` with the operator token. `409
not_ready` while any seat is unready; otherwise the server opens game one, pushes
`{kind: "game_start", game}` and unfreezes the first window. `ww start` calls the same
endpoint. After `game_over` the table stays idle (`{status:"idle"}` to bots) until the
next `/admin/start`; there is no auto-restart under manual-start.

**Game over.** When a game ends the spectator gets `{kind: "game_over", game, result,
summary}` and `game.json` persists the same summary: `winners`, `wolves`, `deaths` in
order with `{seat, day, cause}`, `spend_total`, `broke_seats`, `most_accused` and
`powerups`.

## Finale (`ww final final.toml`)

```toml
seats = 12
seed = 2026
[[teams]]
name = "wolfpack"                      # no repo: a laptop seat (the default on the day)

[[teams]]
name = "silverbullet"
repo = "git@github.com:...#<commit>"   # optional: pinned commit, run by the organiser
cmd = "uv run bot"                     # run inside the checkout
```

- No submission. A team without `repo` is a **laptop seat**: the team runs `uv run bot` on its own
  laptop with the `WW_SERVER` and `WW_TOKEN` the finale prints and writes to
  `runs/final/laptop_seats.json`. Serve with `--host 0.0.0.0` so laptops can reach it.
- Each team gets exactly one seat; baseline house bots fill the rest, and more teams than
  `seats` is refused. Seats are shuffled with the seed.
- Bots never hold a key: every seat calls the server's LLM proxy with its token, and the
  proxy charges that team's Bifrost key.
- Repo seats only: each bot gets only `WW_SERVER`, `WW_TOKEN`, `PATH` and `HOME`. No other env is
  inherited, so no Bifrost key can leak to team code. `uv sync` during install also
  runs with a sanitized env (`PATH`, `HOME`, `UV_CACHE_DIR`, `UV_PYTHON`,
  `UV_INDEX_URL` only).
- Bifrost keys work as in `ww serve`: a team listed in the optional `teams.toml` uses its own key
  env var, and every other team uses `BIFROST_API_KEY`. The finale refuses to start if any
  assigned team's key is unset (`--fake-llm` skips this).
- Bots run in their own process groups; shutdown and replacement kill the group.
- **Readiness barrier:** the first window stays closed until every seat has called
  `get_my_role`, or after `--ready-s` (default 90s). A seat that is not ready by then is
  replaced by a baseline house bot under a fresh token, so a late bot gets `unauthorized`.
  The clock starts only when the barrier opens.
- A repo bot process that exits mid-game is treated as broke for the rest of the game. A laptop
  bot that drops out misses deadlines like any funded seat.
- `ww final --check final.toml` clones, installs and smoke-starts every repo bot, then exits.
- Runbook for the day: `docs/finale.md`.

## CLI

- `ww serve [--format practice|final] [--seats N] [--fake-llm] [--tables N] [--budget-usd X] [--min-window-s S] [--transition-s S] [--discussion-s S] [--max-posts N] [--floor-s S] [--hot-seat-s S] [--hot-seat-answer-s S] [--reveal-s S] [--beat-s S] [--manual-start|--no-manual-start]`
- `ww table --server URL --bots random,quiet,accuser,baseline,...` runs house bots
  against a running server (`--server` is required); it registers its seats with
  `POST /admin/house` (operator token) before starting.
- `ww tokens` prints seat tokens for a table.
- `ww start --server URL` starts a manual-start table waiting in the lobby.
- `ww final [--check] final.toml [--host H] [--port P] [--ready-s S] [--fake-llm] [--min-window-s S] [--transition-s S] [--reveal-s S] [--beat-s S] [--auto-start] [--forgers N]` (pacing defaults: min 10s, transition 6s, reveal 1.5s, beat 2.5s; manual start on)

## Connect Four (`connect4/`)

- Engine (7×6), a solver (negamax with alpha-beta; depth-limited is fine), puzzle sets
  `win_in_1` and `block_in_1` generated from the solver.
- `bots/naive.py`: one LLM call per move through Bifrost (direct, env
  `BIFROST_BASE_URL` + `BIFROST_API_KEY`, OpenAI-compatible; the bot appends `/v1`
  to the bare origin itself), prompt-only.
- `make eval-c4` runs the team's own scorers (`connect4/team_evals/`) through Braintrust
  `Eval()` and prints a scoreboard, including the always-column-0 negative control.
  Our hidden suite lives in `reference/connect4/`.
- `tournament.py`: round-robin between bot modules.

## Starter kit (`starter/`)

- `bot/`: a minimal MCP-driven Werewolf agent that calls the proxy. It plays legally
  but badly. Started with `uv run bot`, reading `WW_SERVER` and `WW_TOKEN`.
- `evals/`: the team-facing checks from the talk doc, reading traces and engine logs.
- `.claude/settings.json` with a PreToolUse hook that blocks edits under `evals/`.
- `make eval`, `make practice` (spins up a local server and house bots; `FORGERS=N` seats forgers).
- The built-in negative control (an always-vote-first-seat transcript) is a smoke
  test: it shows at least one gate catches an obviously bad bot; it does not show
  each scorer measures what it claims.
