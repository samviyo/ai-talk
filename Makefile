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

reveal-alibi:
	uv run python -m alibi.evals --bot $(ALIBI_BOT) --split $(ALIBI_SPLIT) --trials $(ALIBI_TRIALS) --heldout
	uv run python -m alibi.evals --bot $(ALIBI_BOT) --split heldout --trials $(ALIBI_TRIALS) --team-evals-dir $(ALIBI_REFERENCE_DIR)

check-alibi:
	uv run python -m alibi.checks

test-alibi:
	uv run pytest alibi -q

ALIBI_SCORER_FILES = $(wildcard alibi/team_evals/*.py)
ALIBI_DATASET_FILES = $(wildcard alibi/datasets/*.jsonl)

lock-alibi:
	touch alibi/team_evals/.locked
	chmod a-w $(ALIBI_SCORER_FILES) alibi/team_evals/gates.toml $(ALIBI_DATASET_FILES)

unlock-alibi:
	chmod u+w $(ALIBI_SCORER_FILES) alibi/team_evals/gates.toml $(ALIBI_DATASET_FILES)
	rm -f alibi/team_evals/.locked
