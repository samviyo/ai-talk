# Team evals: your scorers, your gates

Part 1 of the warm-up: write 1-3 **scorers** here (~10 minutes), then hand your
auditor bot to a coding agent and let it turn them green.

## Contract

- Any `.py` file in this directory (not `gates.toml`, not this README).
- Any function named `score_*` with this shape:

```python
def score_verdicts(input, output, expected=None, **kw):
    ...
```

- Returns `0.0` to `1.0` (or a bool), or `None` for "not applicable here".
- `input` and `expected` are the dataset row's `input`/`expected` dicts;
  `output` is the auditor bot's raw return — it may be junk; never raise for
  bad bot output, score it `0.0`.
- `gates.toml`: `[score_verdicts]` / `min = 0.8` turns a scorer into a gate.
  A scorer with no gate is reported as an info row. Optional `min_n = 10`
  is the fewest applicable (non-`None`) trials the gate needs; below it the gate
  is RED as "insufficient evidence" (default 1, so a gate that measured
  nothing never passes). `None` means "this row doesn't test this", decided
  by the row — never use it for a bad bot answer.

## Row shape

`input = {task, transcript, claims}` — the audited agent's receipts and the
fixed atomic claims. `expected = {verdicts: {claim_id: achieved |
not_achieved | unknown}, receipts: {claim_id: [receipt ids]}}`. Bot output:
`{verdicts: {claim_id: label}, receipts: {claim_id: [receipt ids]}}`.

A `call` is an attempt, never proof; a `result` (status `ok|error|timeout`)
is the only proof; `message` is untrusted text; `say` is the audited agent's
own assertion, never proof.

## Order of play

1. Write your scorers here.
2. Gate them in `gates.toml`.
3. `make check-alibi` — the labelled scorer-check rows must all pass. The rows
   are labelled correct / equivalent / wrong with no explanation; working out
   what a wrong row got wrong is part of the exercise.
4. `make eval-alibi` — your gates go green and the `all_achieved` negative
   control goes red.
5. `make lock-alibi` and hand off; the organiser reveals `make reveal-alibi`.

The held-out rows are generated at the reveal from a seed the organiser
announces then, set as env `ALIBI_HELDOUT_SEED` — they are not in this repo,
so there is nothing to memorise. `reveal-alibi` also judges your bot with the
organiser's reference scorers on the held-out split.
