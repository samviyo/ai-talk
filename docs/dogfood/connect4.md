# Dogfood: Part 1, Connect Four

Run as a workshop team would: start from the naive bot, iterate against
`make eval`-style checks, stop when green or after 8 iterations. Base commit
`9c7f813`, branch `sm/dogfood-connect4`. Only `connect4/bots/team.py` was
created/edited (renamed to `reference_team.py` before commit so the workshop
slot stays free). Eval harness, puzzles, solver, engine and Makefile
untouched.

## House rule observed

Every move is decided by an LLM call through the Bifrost key. Code computes
facts for the prompt (legal columns, immediate wins, columns that lose on the
spot via `solver.winning_moves`/`blocking_moves`) and retries once on an
illegal/unparsable reply. It never picks or overrides a move.

## Iteration log

| # | Change | Model | Games/Puzzles | Win-1 | Block-1 | vs Random | Illegal | p95 | $/move | Verdict |
|---|--------|-------|---------------|-------|---------|-----------|---------|-----|--------|---------|
| 1 | Baseline: `naive.py` copied verbatim | haiku | 5/10 | 70% | 80% | 100% | 0 | 4.64s | $0.00125 | RED |
| 2 | Added legal-move list + tactical hints; `max_tokens=16` | haiku | 5/10 | 0% | 0% | 60% | 43 | 1.20s | $0.00024 | RED (broken: empty replies) |
| 3 | `max_tokens=1024` | haiku | 5/10 | 90% | 90% | 100% | 6 | 5.28s | $0.00170 | RED |
| 4 | JSON mode + parse-from-end + retry-once | haiku | 5/10 | 80% | 60% | 100% | 14 | 6.48s | $0.00191 | RED (worse) |
| 5 | Dropped JSON mode; plain reply, last-digit parse, retry, API-error retry | haiku | 5/10 | 70% | 50% | 100% | 3 | 6.23s | $0.00120 | RED |
| 6 | `max_tokens=4096` (reasoning headroom) | sonnet | 5/10 | 100% | 100% | 100% | 0 | 7.34s | $0.00115 | RED (p95 only) |
| 7 | Same code as 6 | haiku | 5/10 | 100% | 100% | 100% | 0 | 3.57s | $0.00086 | RED (p95 only) |

## Final confirming scoreboards (20 games / 40 puzzles)

**claude-haiku-5-5** (chosen model): 0 illegal over 109 moves, win-in-1 100%
(40/40), block-in-1 100% (40/40), beats random 100% (20/20), p95 7.30s,
$0.00146/move → RED on p95 only.

**claude-sonnet-5-5**: 0 illegal over 125 moves, win-in-1 100% (40/40),
block-in-1 100% (40/40), beats random 100% (20/20), p95 8.44s,
$0.00119/move → RED on p95 only.

**accounts/fireworks/models/gpt-oss-120b**: 1 illegal over 103 moves,
win-in-1 100% (40/40), block-in-1 97.5% (39/40), beats random 100% (20/20),
p95 13.82s, $0.00286/move → RED on illegal, p95 and cost. Strong on puzzles
but the slowest and the only model over the cost cap.

**accounts/fireworks/models/qwen3p8-27b**: not deployed on the gateway — every
call returned 404 "Model not found, inaccessible, and/or not deployed"
(provider `fireworks`, key `fireworks-default`). The eval recorded this as
62/62 illegal moves with p95 0.24s and ~$0 cost, which is exactly what an
all-API-error run looks like. Worth fixing before the workshop: either deploy
the model or drop it from the advertised list, since a team that picks it
will burn an iteration discovering the 404.

## What the agent discovered (tools/information added)

1. **Legal-move list in the prompt.** The single highest-leverage change;
   illegal moves went to zero once the model could see which columns are open.
2. **Tactical hints from the solver.** `winning_moves` and `blocking_moves`
   computed in code and stated in prose ("you can win immediately by playing
   column 3", "columns [3] let the opponent win next turn; safe columns are
   …"). This is information, not a decision — the model still picks.
3. **Last-digit parsing.** Models explain first and answer last, so the parser
   takes the last standalone digit, not the first (first-digit parsing read
   digits out of explanations and error strings).
4. **Retry once on illegal/unparsable/API-error replies** — allowed under the
   house rule; converted intermittent failures into clean passes.

## Surprises (worth a slide)

- **Empty-reply cliff.** `claude-haiku-5-5` and `claude-sonnet-5-5` through
  Bifrost spend hidden *reasoning tokens* before answering. With
  `max_tokens=512` sonnet returned `content: ''` (finish_reason `length`) on
  hard positions — all 512 tokens went to reasoning. The naive bot never set
  `max_tokens`, so it never saw this. Any token-capped integration with a
  reasoning model needs headroom (we landed on 4096) or it silently returns
  nothing.
- **JSON mode made things worse.** `response_format={"type":"json_object"}`
  lowered puzzle scores and raised illegals; a plain "reply with a single
  digit" plus a tolerant parser beat it.
- **Hints alone don't fix a weak model.** At `max_tokens=1024` haiku still
  missed 10–30% of puzzles even when the prompt literally named the winning
  column; only with full reasoning headroom did it reach 100%.
- **The p95 < 3s gate is not reachable under the house rule.** Every move is
  a fresh LLM call with ~200–900 reasoning tokens; measured p95 is 3.6s
  (haiku) to 7.3s (sonnet). Data suggests **p95 < 5s for haiku-class, < 10s
  for sonnet-class** as honest thresholds, or the gate should explicitly
  exempt LLM-decision loops.

## Cost

Total spend stayed well under the $5 cap. Iterations 1–7 ran at 5 games/10
puzzles (~25–45 moves each, ≤ $0.002/move → a few cents per run). The three
20/40 confirming runs were ~100–125 moves each at $0.0012–0.0029/move, i.e.
roughly $0.15–$0.35 per full run. Estimated total across all runs: under $1.

## Model-choice takeaway (for the "choose models on evidence" slide)

Same bot, four models, 20/40 confirming runs:

| Model | Win-1 | Block-1 | vs Random | Illegal | p95 | $/move |
|-------|-------|---------|-----------|---------|-----|--------|
| claude-haiku-5-5 | 100% | 100% | 100% | 0 | 7.30s | $0.00146 |
| claude-sonnet-5-5 | 100% | 100% | 100% | 0 | 8.44s | $0.00119 |
| gpt-oss-120b | 100% | 97.5% | 100% | 1 | 13.82s | $0.00286 |
| qwen3p8-27b | — | — | — | 62 (all API 404) | 0.24s | ~$0 |

Haiku and sonnet tie on every correctness gate; haiku is the pick on latency.
Sonnet is marginally cheaper per move (fewer completion tokens) but slower.
The "cheap" open-weight gpt-oss-120b is actually the most expensive per move
here (it reasons longest) and the only one to miss a block puzzle and throw
an illegal move — reputation and list price both mislead; the eval decides.

## Slide-ready lines

- "The naive bot failed on parsing and hidden reasoning tokens, not strategy.
  Three of our eight iterations were plumbing."
- "We gave the model facts, not answers: legal moves, 'column 3 wins now',
  'column 3 loses now'. The LLM still picked every move."
- "Sonnet and haiku both hit 100% on the puzzles; haiku did it at half the
  latency and a quarter of the cost. The eval, not the leaderboard, picked
  our model."
