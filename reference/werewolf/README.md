# Organiser reference: the finished 13-gate Werewolf suite

Organiser command to score any team's runs dir (used after the finale):

    cd reference/werewolf && uv run --project ../../starter python -m evals --runs <runs-dir>

`evals/thresholds.toml` holds the gates. `test_evals.py` are its tests over
`fixtures/runs/` (the always-vote-first-seat negative control must fail vote accuracy).
