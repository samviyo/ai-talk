# Your offline scorers

`make check` runs `evals/datasets/puzzles.jsonl` and `spoofs.jsonl` through `decide()`,
then every `score_*` function in this directory's `*.py` files over each reply:

```python
def score_something(input, output, expected=None, **kwargs) -> float | bool | None:
    ...
```

- `input` is the row's `input` dict, `output` the bot's raw reply string, `expected`
  the row's `expected`.
- Every scorer sees both datasets: return `None` on rows it doesn't apply to.
- A reply you can't parse is a fail (`0.0`), not `None`.
- Gate it in `evals/gates.toml`. Each dataset with rows needs at least one gated
  scorer that applies to it, or `make check` is RED.
- One helper ships: `seats_match` (puzzle reply seats == `expected["seats"]`). It's
  info until you gate `[seats_match]`.
