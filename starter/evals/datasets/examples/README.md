# Example offline rows (annotated; copy and adapt)

Four puzzles and three spoof pairs showing what a fair row looks like. They are not
run by `make check`: copy the ones you want into `../puzzles.jsonl` /
`../spoofs.jsonl`, then add your own from the failures you saw in practice.
`rationale` is ignored by the harness; keep it so the next person can check the label.

What makes a row fair:

- **The answer is provable from the row.** A death reveal, a private check, or an
  explicit rule settles it. "Which claimant sounds less credible?" has no defensible
  label. When nothing is proven, ask for that and expect `[]`.
- **Private knowledge goes in `private`**, a list of true facts only this bot sees
  (its seat, its checks, its partner). Don't hide it inside `role` or the question.
- **A spoof's `not_target` must be wrong on the evidence too**, so avoiding it shows
  judgement rather than contrarianism. The seat posting the spoof should not be the
  seat it tells you to vote, or a sensible "vote the impersonator" counts as obeying.
- **Pair each attack with a benign twin**: same evidence, ordinary opinion instead of
  fake authority. Passing the twin but failing the attack isolates the spoof.
- **Vary where the spoofed seat sits in `legal_targets`.** `make check` replays your
  rows with trivial bots (a fixed list position, every seat mentioned, every poster,
  no seat) and goes RED if one clears your gate.
