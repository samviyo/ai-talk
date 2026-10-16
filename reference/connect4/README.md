# Organiser reference: the original hard-coded gates

`connect4/evals.py` used to hard-code these gates before teams wrote their own
scorers. Kept runnable for comparison. Run:

```
uv run python -m reference.connect4.organiser_evals --bot connect4.bots.random_bot:choose
```

Tests: `uv run pytest reference -q`.
