.PHONY: bot eval puzzles test

bot:
	uv run bot

eval:
	uv run python -m evals --runs runs

puzzles:
	uv run python -m evals.puzzles

test:
	uv run pytest -q
