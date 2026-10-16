.PHONY: bot check eval practice puzzles test

bot:
	uv run bot

# Offline gates in seconds: deduction puzzles and moderator spoofs.
check:
	uv run python -m evals.offline

puzzles: check

# N games vs house bots into runs/practice: make practice N=5 [KEEP=1]
practice:
	uv run python -m evals.practice --games $(or $(N),5) $(if $(KEEP),--keep,)

# Score your seat: make eval [RUNS=runs] [SEAT=6]
eval:
	uv run python -m evals --runs $(or $(RUNS),runs/practice) $(if $(SEAT),--seat $(SEAT),)

test:
	uv run pytest -q
