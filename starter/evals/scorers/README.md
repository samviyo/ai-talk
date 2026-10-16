# Your scorers

One Python file per failure mode, any function named `score_*` with the signature:

```python
def score_something(input, output, expected=None, **kwargs) -> float | bool | None:
    ...
```

- `input` is a `GameView`: `input.log` (GameLog, the outcome), `input.traces`,
  `input.seat`, `input.name`.
- Return `1.0` / `0.0` (binary preferred), or `None` when not applicable.
- Gate each scorer in `evals/gates.toml` (`[score_something] min = 0.8`); a
  scorer with no gate is shown as info.
- Raising scores 0 and shows the exception on a RED row.
- `make eval` runs your scorers over every (practice game, scored seat) task,
  then the same suite over the known-bad negative-control bot — your suite is
  red unless at least one of your gated scorers is red there.
