.PHONY: bot eval eval-fresh puzzles test practice

bot:
	uv run bot

# Pass SEAT to score only your seat: make eval SEAT=6
eval:
	uv run python -m evals --runs runs $(if $(SEAT),--seat $(SEAT),)

# Move existing runs aside and re-score nothing stale: make eval-fresh
eval-fresh:
	mkdir -p runs/archive
	mv runs/table* runs/archive/ 2>/dev/null || true
	@echo "archived old runs to runs/archive/; play fresh games, then make eval"

puzzles:
	uv run python -m evals.puzzles

# Run N practice games sequentially: make practice N=5
practice:
	@n=$(or $(N),1); i=0; while [ $$i -lt $$n ]; do \
		echo "=== game $$((i+1))/$$n ==="; \
		(cd .. && uv run ww table --bots baseline,baseline,accuser,quiet,random,baseline,baseline); \
		i=$$((i+1)); \
	done

test:
	uv run pytest -q
