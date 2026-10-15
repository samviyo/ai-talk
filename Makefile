BOT ?= connect4.bots.naive:choose
GAMES ?= 200

.PHONY: eval-c4 test-c4

eval-c4:
	uv run python -m connect4.evals --bot $(BOT) --games $(GAMES)

test-c4:
	uv run pytest connect4 -q
