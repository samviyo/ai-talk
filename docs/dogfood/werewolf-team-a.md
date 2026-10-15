# Dogfood: Part 2, Werewolf — Team "A" (cheap & fast)

Run as a workshop team: start from the starter bot, iterate against the eval
scoreboard, stop when green or after 6 iterations. Base commit `aae5615`,
worktree branch `worktree-agent-a8e7404e1a98b84e7`. Only `dogfood/team_a/bot/`
was edited (plus this doc and `dogfood/team_a/HANDOFF.md`'s Definition of
Finished). `evals/`, `werewolf/`, `starter/`, `connect4/` untouched — verified
`diff -rq dogfood/team_a/evals starter/evals` shows only `__pycache__`.

**Model:** every call is `claude-haiku-5-5` through the server's LLM proxy
(`read_board_model` and `decide_model` both haiku, `max_tokens = 512`).

## Definition of Finished (set before touching the bot)

Over **≥ 12 games** of practice:

| Check | Threshold |
|---|---|
| Protocol errors | 0 |
| Reads board before voting | ≥ 95% |
| Role secrecy violations | 0 |
| Claim contradictions | 0 |
| Vote accuracy | ≥ 45% |
| Seer follow-through | ≥ 60% |
| Budget | never broke |
| Win rate as villager | ≥ 40% |
| Win rate as werewolf | ≥ 50% |
| Deduction puzzles | ≥ 4 / 5 |
| Moderator-spoof fixtures | 0 obeyed |

## Iteration table

All batches are 7-seat / 2-wolf practice games, house lineup
`baseline,baseline,accuser,quiet,random,baseline`, our bot at seat 6.
**Caveat:** the smoke/base/base2/iter1 runs are invalid — the LLM proxy 502'd on
every call (see Findings #1), so those games were played by the server fallback,
not a real model. Valid scoreboards start at iter2.

| Iter | Change | protocol | reads | secrecy | claims | vote acc | seer FT | budget | notes |
|---|---|---|---|---|---|---|---|---|---|
| iter1 | State tracking, role-aware prompts, claimed_role | GREEN | 100% | GREEN | GREEN | 27% RED | 0/3 RED | GREEN | LLM 502'd — invalid; also revealed night-window miss |
| iter2 | + LLM timeout ≤ deadline, max_tokens 512, proxy /v1 fix | GREEN | 100% | RED (ours ×2) | GREEN | 34% RED | 0/4 RED | GREEN | seer check lands (day 2); wolf posts trip secrecy regex |
| iter3 | + wolf never pairs partner#"wolf"; seer claims only with a check, leads with wolf | GREEN | 100% | RED (house ×2) | GREEN | 50% GREEN | 1/2 GREEN | GREEN | our bot 0 violations; reds are house bots |
| iter3b | (validation, new seeds) | GREEN | 100% | RED (house ×1) | GREEN | 51% GREEN | 1/5 RED (house) | GREEN | our bot 0 violations |
| final | (validation, 7 games) | GREEN | 100% | GREEN | GREEN | 42% GREEN | 1/5 RED (house) | GREEN | only red gate is house seers |

## Final scoreboard (verbatim, `uv run python -m evals --runs runs/final`)

```
Scoreboard over runs/final:

  [GREEN] protocol errors            0/7 games over the limit
  [GREEN] reads board before voting  100% of votes preceded by read_board
  [GREEN] role secrecy               0/7 games with violations
  [GREEN] claim consistency          0/7 games with contradictions
  [GREEN] vote accuracy              20/48 = 42% (random ~33%, gate 40%)
  [RED  ] seer follow-through        1/5 identified wolves voted out within a day
  [GREEN] budget never broke         0/7 games with a broke seat
  [info ] win rate by role           doctor 1/7, seer 1/7, villager 3/21, werewolf 12/14

6 gates green, 1 red
```

Puzzles: **4/5** (`two_seers` is flaky — the model sometimes hedges between the
two claimants instead of committing to seat 5). Spoof fixtures: **0/2 obeyed**.

## Definition-of-Finished self-assessment

| Check | Threshold | Met? |
|---|---|---|
| Protocol errors 0 | yes |
| Reads before vote ≥95% | yes (100%) |
| Role secrecy 0 (our bot) | yes — every red instance is a house baseline wolf |
| Claim contradictions 0 | yes |
| Vote accuracy ≥45% | borderline (42–51% across batches) |
| Seer follow-through ≥60% | **no** — but every miss is a house seer; our seer rarely lives to check a wolf |
| Budget never broke | yes |
| Win rate villager ≥40% | no (table is wolf-favored, see Findings #6) |
| Win rate werewolf ≥50% | yes (wolves win ~100% at this table) |
| Puzzles ≥4/5 | yes |
| Spoof 0 obeyed | yes |

The two persistent red gates (`role secrecy`, `seer follow-through`) are driven
entirely by house bots, not by anything our bot controls — see Findings #4/#5.

## Cost and speed

- **Cost per game (our seat):** avg ≈ **$0.0013**, range $0.0002–$0.0028
  (3–12 haiku calls per game). Total across all 42 practice games: **$0.033**.
  Plus ~$0.008 for puzzle/spoof/debug calls. **Grand total ≈ $0.04, far under
  the $4 cap.** House bots are free (no LLM).
- **Time per game:** avg ≈ **85s** wall (range 43–172s), ≈ 2.5 game-days. The
  floor is the 20s window × number of windows; our LLM call (bounded to ≤12s)
  fits inside it.

## Findings for the workshop

Ordered by how much they cost a team.

1. **LLM proxy 502s out of the box with the documented env setup (biggest
   blocker).** `scripts/gateway-env.sh` exports
   `BIFROST_BASE_URL=https://developer-gateway.../v1`, but
   `werewolf/server/llm.py:81` posts to `f"{base_url}/v1/chat/completions"`.
   The double `/v1` makes the gateway return **405**, the proxy swallows it and
   returns **502 `upstream_error`**. Every starter-bot game against the real
   gateway was silently played by the server fallback. Repro: `source
   scripts/gateway-env.sh && uv run ww serve ...`, run any bot, then read
   `runs/.../trace.jsonl` — every `llm` line has `result_code: "502"`. Workaround
   (server process only): `export BIFROST_BASE_URL="${BIFROST_BASE_URL%/v1}"`.
   The real fix is to reconcile the two — either drop `/v1` from gateway-env.sh
   or stop appending it in llm.py. **The workshop should add a one-line smoke
   check** ("call the proxy once, assert non-502") to the practice loop, because
   a bot that never reaches a model still produces a plausible-looking
   scoreboard.

2. **`wait_for_turn` serves each window once per seat, and a slow LLM call makes
   you miss the whole window.** `mcp_tools.py:_wait_for_turn` records
   `_served_turn[seat] = turn_id` on first serve; a re-poll blocks for the *next*
   window. The starter's `OpenAI()` call has no timeout and can outlive the 20s
   window, so our seer woke after night 1 had already been force-abstained
   (`_fallback_abstain` in the trace, zero seer checks). Fix on the bot side:
   `OpenAI(timeout=…, max_retries=0)` capped below `deadline_s`. Worth a sentence
   in the docs: "bound your LLM call to `deadline_s` or you'll abstain."

3. **`runs/` accumulates across batches; there's no per-iteration reset.** To
   compare iterations you must point `--runs-dir` at a fresh directory yourself
   (or `rm -rf` the old one), and the eval reads *every* `runs/<table>/<game>`
   under the path — so a stale batch silently pollutes the next scoreboard. A
   `ww serve --fresh` or an eval `--since` flag would help.

4. **The `role_secrecy` check false-positives on legitimate wolf play.** The
   regex in `evals/checks.py` flags any wolf post matching
   `\b<partner>\b.*\b(wolf|werewolf)\b` — case-insensitive, any order of distance.
   Our wolf posting *"Seat 0 has named seat 4 as a werewolf"* (quoting an
   accusation to deflect) trips it whenever 0 happens to be the partner. It's
   meant to catch a wolf leaking its partner, but it punishes quoting/analysing
   public accusations. Either scope it to first-person reveals or accept that
   wolves can't discuss their partner even to defend them. (We worked around it
   with a prompt rule, which arguably weakens the wolf.)

5. **`role_secrecy` and `seer_follow_through` grade the house bots, not just
   yours.** The `baseline` brain's wolf runs a scripted fake-seer bluff
   (`brains.py` BaselineBrain `COUNTERCLAIM_CHANCE`) that posts
   `"I am the seer: <random seat> werewolf"` — and that random seat is sometimes
   its own partner, producing a secrecy violation no team bot can prevent.
   Likewise the seer-follow-through gate counts house seers who find a wolf but
   never persuade the table. So a team can ship a perfect bot and still see red.
   Consider scoping these two gates to the team seat, or documenting that they're
   table-level and may be unreachable against house bots.

6. **The practice table is heavily wolf-favored.** With
   `baseline,baseline,accuser,quiet,random,baseline` + one team bot, the village
   almost never coordinates a lynch (random/quiet/accuser don't follow a seer),
   and the seer is usually night-killed within a day of claiming. Result: wolves
   won 12/12 and 12/14 in two batches regardless of our bot. Win-rate thresholds
   in HANDOFF.md are hard to set meaningfully until the table is balanced; a
   "villager ≥40%" bar was unreachable here. Worth telling teams what a realistic
   win-rate looks like against this specific house lineup.

7. **The `evals.puzzles` / `evals.spoof` harness has no offline path.** The
   default `--module yourbot.decide` doesn't exist in the starter, and `decide()`
   needs a *live server's* LLM proxy (`WW_SERVER`/`WW_TOKEN`) — there's no
   `--fake-llm` or direct-gateway mode for puzzles. We added
   `bot/puzzle_decide.py` and ran it against a throwaway `ww serve`. A documented
   way to point puzzles at the gateway directly (or a `--fake-llm` puzzle server)
   would remove that friction.

8. **Minor: `vote accuracy` and `reads board before voting` also mix house seats
   into the denominator**, which dilutes the team signal the same way. Readable
   but worth knowing when you set thresholds.

9. **House bots spend $0** (they make no LLM calls), so `game.json`'s `spend`
   map is dominated by the team seat — convenient for cost accounting, but the
   per-seat `broke` events and the 402 path are hard to exercise without a team
   bot that actually overspends. We never went broke (max $0.0028/game vs the
   $0.50 default), so the broke/fallback path is effectively untested by a
   careful bot.
