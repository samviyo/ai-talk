BOT ?= connect4.bots.naive:choose
GAMES ?= 200
PUZZLES ?= 40
TRIALS ?= 1

.PHONY: eval-c4 test-c4 lock-c4 unlock-c4

eval-c4:
	uv run python -m connect4.evals --bot $(BOT) --games $(GAMES) --puzzles $(PUZZLES) --trials $(TRIALS)

test-c4:
	uv run pytest connect4 -q

C4_SCORER_FILES = $(wildcard connect4/team_evals/*.py)

lock-c4:
	touch connect4/team_evals/.locked
	chmod a-w $(C4_SCORER_FILES) connect4/team_evals/gates.toml

unlock-c4:
	chmod u+w $(C4_SCORER_FILES) connect4/team_evals/gates.toml
	rm -f connect4/team_evals/.locked

ALIBI_BOT ?= alibi.bots.naive:audit
ALIBI_SPLIT ?= dev
ALIBI_TRIALS ?= 1

.PHONY: eval-alibi reveal-alibi check-alibi test-alibi lock-alibi unlock-alibi

eval-alibi:
	uv run python -m alibi.evals --bot $(ALIBI_BOT) --split $(ALIBI_SPLIT) --trials $(ALIBI_TRIALS)

ALIBI_REFERENCE_DIR ?= reference/alibi

# Both legs always run: a RED team suite must not hide the organiser's verdict.
reveal-alibi:
	@rc=0; \
	uv run python -m alibi.evals --bot $(ALIBI_BOT) --split $(ALIBI_SPLIT) --trials $(ALIBI_TRIALS) --heldout || rc=1; \
	uv run python -m alibi.evals --bot $(ALIBI_BOT) --split heldout --trials $(ALIBI_TRIALS) --team-evals-dir $(ALIBI_REFERENCE_DIR) || rc=1; \
	exit $$rc

check-alibi:
	uv run python -m alibi.checks

test-alibi:
	uv run pytest alibi -q

ALIBI_SCORER_FILES = $(wildcard alibi/team_evals/*.py)
ALIBI_DATASET_FILES = $(wildcard alibi/datasets/*.jsonl)

lock-alibi:
	chmod u+w alibi/team_evals
	touch alibi/team_evals/.locked
	chmod a-w $(ALIBI_SCORER_FILES) alibi/team_evals/gates.toml $(ALIBI_DATASET_FILES)
	chmod a-w alibi/team_evals alibi/datasets

unlock-alibi:
	chmod u+w $(ALIBI_SCORER_FILES) alibi/team_evals/gates.toml $(ALIBI_DATASET_FILES)
	chmod u+w alibi/team_evals alibi/datasets
	rm -f alibi/team_evals/.locked

# Part 1 migration: port check-alibi to Zig; frozen golden outputs are the only reviewer.
ALIBI_ZIG_BIN ?=
ZIG_BIN_FLAG = $(if $(ALIBI_ZIG_BIN),--bin $(ALIBI_ZIG_BIN),)
# The port runs fixed scorers we ship, so teams only write Zig.
ZIG_SCORERS = ALIBI_TEAM_EVALS_DIR=alibi/zig/scorers

.PHONY: setup zig zig-build golden parity parity-parse parity-score parity-report reveal-parity unlock-golden fake-port

# One command for a fresh laptop: Python deps, the pinned Zig, and a build of the stubs.
setup:
	@command -v uv >/dev/null || { echo "uv not found: install it from https://docs.astral.sh/uv/"; exit 1; }
	uv sync
	cd starter && uv sync
	uv run python -m alibi.migrate zig
	uv run python -m alibi.migrate build
	@echo "setup done: next is make golden at the hand-off"

zig:
	uv run python -m alibi.migrate zig

zig-build:
	uv run python -m alibi.migrate build

golden:
	$(ZIG_SCORERS) uv run python -m alibi.migrate golden

parity:
	uv run python -m alibi.migrate parity $(ZIG_BIN_FLAG)

parity-parse:
	uv run python -m alibi.migrate parity --stage parse $(ZIG_BIN_FLAG)

parity-score:
	uv run python -m alibi.migrate parity --stage score $(ZIG_BIN_FLAG)

parity-report:
	uv run python -m alibi.migrate parity --stage report $(ZIG_BIN_FLAG)

reveal-parity:
	$(ZIG_SCORERS) uv run python -m alibi.migrate reveal $(ZIG_BIN_FLAG)

unlock-golden:
	chmod -R u+w alibi/golden
	rm -f alibi/golden/.locked

# Organiser-only: a lookup-table "port" that passes parity and fails reveal-parity.
fake-port:
	uv run python reference/alibi/zig_port/fake_port.py
