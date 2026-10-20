# Port check-alibi to Zig

Your scorers are locked. Next job: port the tool that checks them, `make check-alibi`
(`alibi/checks.py`, your `alibi/team_evals/*.py` and `alibi/scorers.py`), from Python to Zig.
Nobody in the room reads Zig, so nobody can review the port. The golden outputs are the
only reviewer.

**Done** = `make parity` is green, and it stays green on fresh held-out inputs revealed at
the end (`ALIBI_HELDOUT_SEED=<seed> make reveal-parity`). You have 20 minutes.

## Setup (once)

```sh
make zig        # pinned Zig 0.16.0 into .tools/
make golden     # run the Python tool on today's inputs, freeze the answers, lock them
```

`make golden` refuses to run until your scorers are locked (`make lock-alibi`), and once it
has run it refuses to run again. Like your scorers, `alibi/golden/` is locked: agents cannot
edit it, and they cannot edit the Python tool either.

## The port

Four files in `alibi/zig/src/`. Each prints one JSON object, so each piece can be checked on
its own:

| File | Command | Check | Ports |
|---|---|---|---|
| `parse.zig` | `alibi-check parse <rows.jsonl> <gates.toml>` | `make parity-parse` | `load_check_rows`, `load_gates` |
| `score.zig` | `alibi-check score <parse.json>` | `make parity-score` | your `score_*` functions, `wrapped` |
| `report.zig` | `alibi-check report <parse.json> <score.json>` | `make parity-report` | the rule in `evaluate` |
| `main.zig` | `alibi-check run <rows.jsonl> <gates.toml>` | `make parity` | join-up: parse, then score, then report |

The stage checks feed each stage the golden output of the stage before it, so `score.zig`
can go green before `parse.zig` works.

## Shapes

```text
parse  -> {"gates": {name: {"min": number, "min_n": int}},
           "rows": [{"id", "label", "input", "output", "expected"}]}      expected is null if absent
score  -> {"gated": [name], "values": {scorer: {row_id: number | null | "error"}}}
report -> {"green": bool, "rows": [{"id", "label", "ok"}],
           "failures": [{"kind", "row", "scorer"}]}
run    -> score and report keys in one object
```

- `gated` lists scorers that have a gate, in the order Python loads them: module file name,
  then function name.
- A value is `null` when the scorer returns `None`, and `"error"` when it raises or returns
  something that is not a number.
- `kind` is one of `raised`, `must_pass`, `slips_through`, `unknown_label`, `constant`,
  `no_gates`. Use `null` for a `row` or `scorer` that does not apply.

Parity compares numbers to within 1e-9, ignores key order, and ignores the order of
`failures`. Everything else must match.

## Commands

```sh
make zig-build                                  # build alibi/zig/zig-out/bin/alibi-check
make parity                                     # end to end against every golden case
make parity-parse | parity-score | parity-report
ALIBI_HELDOUT_SEED=<seed> make reveal-parity    # fresh inputs, Python vs Zig
```

Run them from the repo root. The binary can read golden files directly:
`alibi/zig/zig-out/bin/alibi-check run alibi/golden/checks/rows.jsonl alibi/golden/checks/gates.toml`.
