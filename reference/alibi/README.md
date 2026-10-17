# Organiser reference: The Agent's Alibi

The organiser's reference scorers, runnable through the same harness the teams
use. The loader dir is overridable by env `ALIBI_TEAM_EVALS_DIR` or the
`--team-evals-dir` flag:

```
uv run python -m alibi.checks --team-evals-dir reference/alibi
ALIBI_TEAM_EVALS_DIR=reference/alibi uv run python -m alibi.evals --bot alibi.bots.naive:audit
```

Tests: `uv run pytest reference/alibi -q`.
