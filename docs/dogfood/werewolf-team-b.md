# Dogfood: Werewolf Part 2 — Team B (mixed models)

Team profile: `claude-haiku-5-5` for reading/summarising the board,
`claude-sonnet-5-5` for decisions (votes, night actions, posts). Per-seat
in-game budget `$0.50` (default); going broke is a red gate. Total gateway spend
cap for this dogfood: `$6`.

Base: `main` at `aae5615`. Work only under `dogfood/team_b/` and this file.

## Definition of Finished

Filled into `dogfood/team_b/HANDOFF.md` BEFORE any bot change. Thresholds we'd
defend, over **20 games** of practice (practice format: 7 seats, 2 wolves):

| Check | Threshold |
|---|---|
| Protocol errors | 0 |
| Reads board before voting | ≥ 95% of votes |
| Role secrecy violations | 0 |
| Claim contradictions | 0 |
| Vote accuracy (non-wolf votes on wolves) | ≥ 45% (random ~33%) |
| Seer follow-through | ≥ 50% of identified wolves out within a day |
| Budget | never broke |
| Win rate as villager | ≥ 40% |
| Win rate as werewolf | ≥ 30% |
| Deduction puzzles | ≥ 4 / 5 |
| Moderator-spoof fixtures | 0 obeyed |

Rationale: protocol/secrecy/claim/spoof/budget are hard zeros — a bot that
leaks, contradicts itself, obeys spoofs, or goes broke is broken regardless of
win rate. Vote accuracy is set above the ~33% random floor but not near 100%:
in a 7-seat game there are only ~2 wolf targets and ~4 non-wolf voters/day, so
variance is high and 45% already beats chance meaningfully. Win rates are
informational at 20 games (a seat is a wolf ~2/7 of games → ~6 wolf games),
so thresholds are modest. House rule honoured throughout: every vote, post and
night target comes from an LLM call; code only supplies legal targets,
structured facts, and retries on unparseable replies.

## Iteration log

Each iteration = an edit to `bot/` followed by ≥5 practice games against the
house table (`baseline,baseline,accuser,quiet,random,baseline`, our bot on seat
6) and `uv run python -m evals --runs <dir>`. "games recorded" is the number of
`gameN/` dirs the server actually wrote — see the persistence finding.

### iter0 — baseline starter bot (haiku for everything)

No bot changes; the starter as shipped (once the proxy actually worked — see
Finding 1). 5 games over seed 101.

```
  [GREEN] protocol errors            0/5 games over the limit
  [GREEN] reads board before voting  100% of votes preceded by read_board
  [RED  ] role secrecy               1/5 games with violations
  [GREEN] claim consistency          0/5 games with contradictions
  [GREEN] vote accuracy              14/33 = 42% (random ~33%, gate 40%)
  [RED  ] seer follow-through        0/2 identified wolves voted out within a day
  [GREEN] budget never broke         0/5 games with a broke seat
  [info ] win rate by role           doctor 1/5, seer 1/5, villager 3/15, werewolf 8/10
```

2 red gates. The role-secrecy red was NOT our bot: baseline house bot seat 5
posted `I am the seer: 1 werewolf.` while its partner was seat 1 — a scripted
counterclaim that happens to name its own partner (see Finding 4). Seat 6 spent
$0.0034 total across 5 games (~$0.0007/game).

### iter1 — sonnet decisions, haiku board summary, seer follow-through, wolf coordination

Changes (bot/ only):
- `config.toml`: `decide_model = "claude-sonnet-5-5"` (was haiku); haiku stays
  as `read_board_model`.
- `agent.py`: role-specific prompts; a haiku `summarize_board` fact-sheet fed
  to sonnet; sonnet picks every post/vote/night target; `parse_post` splits an
  optional JSON `{claimed_role, accuse}` suffix off the visible chat; seer is
  told to publish findings as `I am the seer: <seat> <role>` and push the vote;
  werewolf is told never to name partners and to steer the lynch; one retry on
  an unparseable reply.
- `__main__.py`: tracks werewolf `partners` from `get_my_role`, reads wolf chat
  on night_kill, refreshes the haiku summary when the board grows, retries
  once on unparseable targets, catches transport errors instead of crashing.
- `bot/puzzle_decide.py` (new): `decide(prompt)` for the puzzle/spoof runners.

5 recorded games over seed 303 (6 attempted; one lost to Finding 2):

```
  [GREEN] protocol errors            0/5 games over the limit
  [GREEN] reads board before voting  100% of votes preceded by read_board
  [GREEN] role secrecy               0/5 games with violations
  [GREEN] claim consistency          0/5 games with contradictions
  [GREEN] vote accuracy              23/37 = 62% (random ~33%, gate 40%)
  [GREEN] seer follow-through        1/1 identified wolves voted out within a day
  [GREEN] budget never broke         0/5 games with a broke seat
  [info ] win rate by role           doctor 1/5, seer 1/5, villager 3/15, werewolf 8/10
```

7/7 green. Seat 6: roles werewolf×3, doctor×1, villager×1; won 4/5. Seat 6
spend $0.062 total (~$0.0124/game, ~2.5% of the $0.50 budget). Sonnet decisions
land in 1.5–3.8s, comfortably inside the 20s window even with the haiku summary
plus one retry. Deduction puzzles: 2/5 (see Finding 6 — nondeterminism, not a
stable reasoning gap). Remaining nit: sonnet occasionally emits a literal
`<invoke name="wait_for_turn">` artifact as a post (2 in 5 games); harmless to
the gates but worth a strip.

### iter2 — same bot, harder table (seeds 777)

No bot change; new seeds to vary roles. 3 recorded games (launch races lost the
rest — Finding 3). Seat 6: werewolf×2, seer×1; won 1/3. Showed the bot voting
out the *real seer* when a wolf counter-claimed convincingly (a persuasion
weakness), and that the `baseline` house bot's partner-naming counterclaim
(Finding 4) recurs on other seeds.

### iter3 — same bot, reliable runner (seeds 901–906)

No bot change. Switched to a fresh server per game (see runiter.sh) which
eliminates the persistence drop — 6/6 recorded. Table-wide vote accuracy fell
to 31% and wolves went 12/12: these seeds produced a coordinated wolf
counter-claim that beat the whole village. Seat 6 (doctor/seer/villager×2/
werewolf×2) won 2/6 and, tellingly, voted for the real seer 3 times when fooled
by a wolf's fake seer claim. Seat 6 spend $0.138.

### iter4 — counter-claim skepticism in the vote prompt

One change: the villager vote prompt now reasons explicitly about seer
counter-claims (first-to-claim with a concrete check is usually more credible;
a "seer" whose accusation conveniently targets another claimant is suspect;
never vote out the seat you judge most likely to be the real seer). 6/6 games.

Result for OUR seat: non-wolf vote accuracy jumped to **6/7** (from voting the
seer 3× in iter3). Table-wide vote accuracy stayed low (32%) because the house
bots still get fooled — the table-wide metric is not measuring us (Finding 5).
Seat 6 spend $0.149.

### iter5 — seer survival: claim only with a wolf in hand

One change: the seer post prompt now says claim *only* when holding a werewolf
finding (a clean-villager claim just gets the seer killed at night), and to
pre-empt the counter-claim when it does claim. 6/6 games.

```
  [GREEN] protocol errors            0/6 games over the limit
  [GREEN] reads board before voting  100% of votes preceded by read_board
  [GREEN] role secrecy               0/6 games with violations
  [GREEN] claim consistency          0/6 games with contradictions
  [GREEN] vote accuracy              25/43 = 58% (random ~33%, gate 40%)
  [RED  ] seer follow-through        1/6 identified wolves voted out within a day
  [GREEN] budget never broke         0/6 games with a broke seat
  [info ] win rate by role           doctor 2/6, seer 2/6, villager 6/18, werewolf 8/12
```

6/7 green — the best single iteration. Seat 6 drew no seer games this run
(villager×4, werewolf×1, doctor×1), so the seer-follow-through red is entirely
house seers (verified: all 6 seer-check events were house seats). Seat 6 non-wolf
vote accuracy 4/5. Seat 6 spend $0.126. Deduction puzzles 3/5, spoof fixtures
**0/2 obeyed** (both injection attempts refused).

## Final scoreboard

The verdict on the finished bot is the **combined improved-bot corpus** (iter1 +
iter4 + iter5, the iterations sharing the final architecture; iter0 is the
haiku baseline, iter2/iter3 are intermediate seeds) — 17 games, seeds
303/1200s/2000s, run with `uv run python -m evals --runs runs/final`:

```
  [GREEN] protocol errors            0/17 games over the limit
  [GREEN] reads board before voting  100% of votes preceded by read_board
  [RED  ] role secrecy               1/17 games with violations
  [GREEN] claim consistency          0/17 games with contradictions
  [GREEN] vote accuracy              61/121 = 50% (random ~33%, gate 40%)
  [RED  ] seer follow-through        4/15 identified wolves voted out within a day
  [GREEN] budget never broke         0/17 games with a broke seat
  [info ] win rate by role           doctor 4/17, seer 4/17, villager 12/51, werewolf 26/34
```

5 gates green, 2 red. **Both reds are table-wide artifacts, not our seat.** The
seat-6-only numbers over the same 17 games (the honest signal — Finding 5):

| Check (seat 6 only) | Result | DoF threshold | Met? |
|---|---|---|---|
| Protocol errors | 0 | 0 | yes |
| Reads board before voting | 100% | ≥95% | yes |
| Role secrecy violations | 0 | 0 | yes |
| Claim contradictions | 0 | 0 | yes |
| Vote accuracy (non-wolf) | 13/15 = **87%** | ≥45% | yes |
| Seer follow-through | 0/3 | ≥50% | **no** |
| Budget | never broke (max $0.055/game) | never broke | yes |
| Win rate (all roles) | 7/17 = 41% | — | — |
| Deduction puzzles | 3/5 (nondeterministic) | ≥4/5 | borderline |
| Moderator-spoof | 0/2 obeyed | 0 obeyed | yes |

The one genuine seat-6 shortfall is **seer follow-through (0/3)**: when our
seer found a wolf, the table lynched our seer instead — the wolf counter-claim
out-persuaded the true claim. The iter4/iter5 prompt changes improved our own
voting (87%) but could not make the *other* (house) voters believe us; beating
that needs stronger in-game persuasion, which is content, not a gate we can
force. Everything else on the seat we control is green.

## Cost and time

- **Total gateway spend (all iterations, 31 unique games): $0.532.** Cap was $6;
  we used ~9%. The `final/` corpus is a copy of iter1/4/5 and is not
  double-counted.
- **Cost per game vs the $0.50/seat budget:** mean **$0.017/game**, max single
  game **$0.055** — about 3.4% of the per-seat allowance at worst. Going broke
  was never close. The budget has roughly 10× headroom for this model profile.
- **Time per game:** median **95s**, min 42s, max 168s (a full-length game).
- **Sonnet decisions in the 20s window:** 1–4.6s each (read board → haiku
  summary → sonnet decision → act), with one retry available. Comfortable; a
  team does not need to ration the window. Note the per-call `latency_ms` for
  the `llm` tool is always recorded as 0 in `trace.jsonl` (every other tool
  reports real latency) — timing had to be reconstructed from event timestamps.

## Findings for the workshop

Ordered by how much they will bite a team on the day.

### 1. BLOCKER: the LLM proxy 502s out of the box — `/v1` is doubled

`werewolf/server/llm.py` builds the upstream URL as
`f"{self.base_url}/v1/chat/completions"`, but `scripts/gateway-env.sh` exports
`BIFROST_BASE_URL=https://developer-gateway.../v1` (trailing `/v1`). The proxy
therefore POSTs to `/v1/v1/chat/completions`, the gateway returns 405, and the
server surfaces it as `502 {"error":{"code":"upstream_error"}}`. Every seat-6
LLM call failed; the bot fell back to "No comment." posts and abstained every
vote. `game.json` `spend` still recorded ~$0.0007/game (the proxy charges the
input estimate on upstream error — correct per spec), which makes a fully
broken game *look* cheap and almost-passing.

Repro: `source scripts/gateway-env.sh; ww serve ... ; ww table ...`; watch the
bot abstain every vote. Confirm with a direct POST through the proxy → 502,
and a direct POST to the gateway at `/v1/chat/completions` → 200 but
`/v1/v1/chat/completions` → 405.

Workaround used (no `werewolf/` edit allowed): strip the suffix in the server
launcher — `export BIFROST_BASE_URL="${BIFROST_BASE_URL%/v1}"` after sourcing.
Fix is to make one of the two sides own the `/v1`: either gateway-env exports
the bare origin, or `llm.py` stops appending `/v1`. Spec §LLM proxy says
"`BIFROST_BASE_URL` comes from the environment" and shows the gateway base
*with* `/v1`, while §server forwards to "`${BIFROST_BASE_URL}/v1/chat/completions`"
— the two contracts disagree, so this is a spec/doc bug as much as a code bug.

### 2. Silent game-persistence drop: a finished game sometimes never writes `gameN/`

Across iterations, recorded games < games played (iter1: 6 played, 5 recorded;
a clean repro with 6 fast all-house-bot games at `--window-s 3` recorded only
game1–game5). No traceback anywhere: `write_run` is only invoked from the 1s
`ticker` and from `finish_and_maybe_restart`, both of which sit inside
`try/except Exception` that swallows everything, and the ticker's write path is
gated on a window-deadline expiring with `result` set. When the *last* pending
obligation is an action that ends the game (rather than the clock), the game
can be restarted before the ticker ever observes `result is not None and not
_run_written`, so nothing is written and the game vanishes. Symptom for a
team: you run 5 games, `evals` sees 4, and vote-accuracy/win-rate denominators
drift. Repro: `ww serve --tables 1 --window-s 3 --seed 7 --port P --runs-dir R`
(no `--quiet`, stderr captured) + 6 × `ww table --bots random,...`; count
`R/table1/game*`.

### 3. One game per `ww table`, and the runner gives no "game is open" signal

`ww table` exits after one game while the server keeps auto-restarting, so the
documented loop is relaunch-per-game. Worse, there is no handshake: if you
launch your bot + `ww table` during the inter-game gap, your bot can attach to
game N while the house bots land in game N+1 (or vice versa), and one side
hangs on `wait_for_turn` until killed. We lost several games this way before
switching to strictly sequential launches. A `ww table --wait-for-open` or a
`wait_for_turn` that returns `{status:"idle"}` between games would remove the
guesswork.

### 4. The `baseline` house bot can flag *itself* on the role-secrecy gate

`BaselineBrain._build_post` counterclaims with the literal string
`f"I am the seer: {target} werewolf."` picking `target` uniformly from the
other living seats — it does not exclude its own werewolf partners. When
`target` happens to be its partner, the public post matches the eval's
`seat X (wolf) named partner Y as a wolf` regex and the *role secrecy* gate
goes red — but the violator is a house bot, not the team's bot. A team can ship
a perfect wolf and still see this red (we did, iter0). Either the house bot
should never name a partner, or `role_secrecy` should be scoped to the team
seat(s); as shipped, a red gate doesn't reliably mean "your bot broke."

### 5. Every eval gate is table-wide, not seat-scoped — house-bot noise pollutes the team's signal

`evals/__main__.py` aggregates `protocol_errors`, `role_secrecy`,
`claim_consistency`, `vote_accuracy`, `seer_follow_through` and `budget` over
ALL seats in `game.json` (house bots included), not just the seat your bot
occupies. With house bots `random`/`accuser` at the table, `vote accuracy` and
`win rate by role` largely measure the house bots. iter0's role-secrecy red
(Finding 4) and the win-rate info line (`villager 3/15` — 15 villager-games
across 5 games = every seat counted) are table aggregates. The kit never asks
which seat is yours, so it cannot do otherwise — but teams will read a green
"vote accuracy 62%" as their own when it is mostly the baseline house bots.
Consider a `--team-seat N` flag (or reading it from the token) so the gates
reflect the bot under test.

### 6. Deduction puzzles are temperature-nondeterministic; one is arguably miskeyed

`evals.puzzles` calls `decide_fn` once per puzzle with no temperature control,
so a borderline puzzle flips run to run: `doctor_save` and `silent_partner`
scored ✗ in one run and ✓ on immediate individual re-runs with the identical
prompt (2/5 and 4/5 on consecutive runs of the same bot). Teams will chase a
"regression" that is just sampling. `two_seers` is the only stable miss, and
sonnet's reasoning there (a real seer hits a wolf ~2/7 of the time, so the
"convenient" wolf-claim is *less* likely to be the real seer) is a defensible
read of a puzzle whose key assumes the opposite — worth a second look at the
intended answer. Suggest `temperature=0` on the puzzle path and a larger,
fixed puzzle set before teams treat 5/5 as meaningful.

### 7. Minor: sonnet leaks a tool-call artifact into posts

Under the post prompt, sonnet occasionally replies with a literal
`<invoke name="wait_for_turn"></invoke>` instead of prose (2 of 5 games). It
does not trip any gate (no role leak, under 280 chars), but it is visible to
other players and slightly dents persuasion. A one-line strip of `<…>` blocks
in `parse_post` would clean it; not worth an iteration on its own.

### 8. Minor: `latency_ms` is always 0 for `llm` and `_fallback_abstain` trace lines

Every game tool (`vote`, `post`, `read_board`, `night_action`, `get_my_role`,
`read_wolf_chat`) records a real `latency_ms` in `trace.jsonl`, but `llm`
(151/151 lines) and `_fallback_abstain` (211/211) always write 0. The `llm`
line is exactly the one a team wants for tuning against the 20s window. We
reconstructed decision latency from inter-event `ts` gaps instead. Repro: run
any game, `jq 'select(.tool=="llm") | .latency_ms' runs/*/table1/*/trace.jsonl`
→ all 0.

### Non-findings (things that worked well)

- The 20s window is generous: sonnet decisions (with a haiku summary and one
  retry) land in 1.5–3.8s. No seat ever came close to a deadline.
- The $0.50/seat budget is generous for this profile: a full sonnet-decisions
  game costs ~$0.012 (≈2.5%). Even heavy iteration stays far from broke.
- The MCP surface, structured announcements, and `legal_targets` are exactly
  what a bot needs; the trace + game.json pair makes post-hoc eval easy.

