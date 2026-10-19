# Team evals: your scorers, your gates

Part 1 of the workshop: write 1-3 **scorers** here (~10 minutes), then hand
your bot to a coding agent and let it turn them green.

## Contract

- Any importable Python module in this directory (not `gates.toml`, not this README).
- Any function named `score_*` with this shape:

```python
def score_win_in_one(input, output, expected=None, **kw):
    ...
```

- Returns `0.0` to `1.0` (or a bool), or `None` for "not applicable here".
- Each scorer runs over **every** dataset; decide applicability yourself.
- `gates.toml`: `[score_win_in_one]` / `min = 0.95` turns a scorer into a gate.
  A scorer with no gate is reported as an info row.

## Datasets (rows = `input` + `expected`)

- `puzzles_win`, `puzzles_block`: `input = {board, player, legal}`,
  `expected = {columns: [...]}` — the solver's correct columns.
  Task output: `{column, latency_s, prompt_tokens, completion_tokens}`.
- `vs_random`: `input = {seed, first}` — a full game vs random_bot.
  Task output: `{winner, moves: [{column, legal, latency_s, prompt_tokens,
  completion_tokens}]}`.

A `move` is your bot's; `moves` is the game transcript. `winner` is `1` if your
bot won, `-1` if random won, `0` for a draw.

## Gates and the scoreboard

Run `make eval-c4 BOT=yourbot:choose GAMES=20 PUZZLES=40 TRIALS=1`.
Each gate prints GREEN/RED with its mean and n. Your suite must fail
`column0_bot` (negative control) or the run is RED. `BT_SEND=1` sends results
to Braintrust; the default is offline. Red exits nonzero.

## Skeleton

```python
def score_win_in_one(input, output, expected=None, **kw):
    ...
```
