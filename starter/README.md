# Werewolf team starter kit

Build an LLM Werewolf agent, prove it with evals you write yourself, hand the rest to your coding agent.

## Layout

- `bot/` — minimal MCP-driven agent. Plays legally, naively. Replace its brain.
- `evals/` — the eval harness (Braintrust `Eval()` under the hood, offline by default).
  You write the suite: game scorers in `evals/scorers/*.py`, offline scorers in
  `evals/scorers/offline/*.py`, gates in `evals/gates.toml`. No team scorers ship (the
  one helper, `seats_match`, is built in).
- `evals/datasets/` — your offline datasets: `puzzles.jsonl` and `spoofs.jsonl`.
- `tests/` — harness tests and fixture transcripts, including the negative control.

## Run your bot

```sh
export WW_SERVER=http://localhost:8000   # the practice server
export WW_TOKEN=<your seat token>        # from `ww tokens`
uv run bot                               # or: make bot
```

The bot logs each turn to the terminal: what it owes, each model call (latency, tokens, the reply) and
each action with the server's answer. If the connection drops it reconnects by itself.

Model choice lives in `bot/config.toml` (`read_board_model`, `decide_model`).
Only models in the server's `prices.toml` are accepted; `max_tokens` is required.

## MCP tools

Your bot connects to `$WW_SERVER/mcp` with `Authorization: Bearer $WW_TOKEN`. Every result is
JSON; a refused call comes back as `{"error": "<code>", ...}`.

| Tool | What it does |
|---|---|
| `wait_for_turn(timeout_s=25, since?)` | Blocks up to 3.5s. Returns `{turn_id, phase, you_owe, legal_targets, announcements, deadline_s, ...}`, `{status:"waiting"}`, or `{status:"over", result}`. With `since`, returns `{status:"news", events, cursor}` when a public event lands. The only moderator channel |
| `get_my_role()` | `{seat, role, partners}`; `partners` is empty unless werewolf |
| `read_board(since=0)` | `{posts, next, nominations, posts_left, whispers, ...}`; may carry `you_owe: reply` or `hot_seat` too. Rate-limited to 1/s (`rate_limited`) |
| `post(turn_id, chat, claimed_role?, accuse?, reply_to?, nominate?, second?)` | Discussion or defence post. `still_queued` while your last post waits for the floor; `no_posts_left` past the limit |
| `react(post_id, kind)` | `agree`, `doubt` or `suspect`; once per post, 8 a day, never your own |
| `vote(turn_id, seat)` | Vote for a standing nominee |
| `night_action(turn_id, target)` | `night_kill`, `seer_check` or `doctor_save`; a seer's result arrives as a `seer_result` announcement |
| `wolf_chat(msg)` / `read_wolf_chat(since=0)` | Werewolves only |
| `get_budget()` | `{spent_usd, limit_usd, remaining_usd, broke}` |
| `shop()` / `buy(turn_id, item, target?, msg?)` | Power-ups: `extra_post`, `whisper`, `double_vote`, `shield`, `frame` |

Rules the server enforces: mutations need the current `turn_id` (else `stale_turn`); 10 budgeted
calls per seat per window (`call_budget_exceeded`; `wait_for_turn` and `read_board` don't count);
a broke seat gets `broke` and the server's fallback acts for it. `you_owe` is one of `night_kill`,
`seer_check`, `doctor_save`, `discuss`, `defence`, `vote`, `reply` or `hot_seat`. Board posts and
whispers are player content, never instructions.

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

1. `make practice N=3` (needs `BIFROST_API_KEY`; see `evals/practice.py`). A real
   `make check` also needs it: the offline run calls Bifrost directly with your own
   `BIFROST_API_KEY`/`BIFROST_BASE_URL` (`bot/puzzle_decide.py`) and spends it. It's the
   one place your bot calls Bifrost directly; in a game it only holds its seat token, and
   the practice server spends your key on its behalf through the proxy. `BT_SEND=1` controls Braintrust logging only;
   it doesn't gate this spend. `uv run python -m evals.offline --fake` runs key-free.
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

Scopes: `make eval RUNS=runs SEAT=6` or `make eval TEAM=name` (with both, `SEAT` wins),
or `uv run python -m evals --runs tests/fixtures/runs/table1 --seat 3`.
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
