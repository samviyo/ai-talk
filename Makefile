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
