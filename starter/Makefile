# The Bifrost key make setup saved; an exported BIFROST_API_KEY still wins.
ifndef BIFROST_API_KEY
-include ../.bifrost.env
export BIFROST_API_KEY BIFROST_BASE_URL
endif

.PHONY: bot check eval practice lock unlock test

bot:
	uv run bot

# Offline evals: your datasets in evals/datasets/*.jsonl through decide().
# TRIALS=3 make check  ->  3 trials per task, reports pass rate and pass^k.
check:
	uv run python -m evals.offline

# N games vs house bots into runs/practice: make practice N=5 [KEEP=1] [FORGERS=2]
practice:
	uv run python -m evals.practice --games $(or $(N),5) $(if $(KEEP),--keep,) $(if $(FORGERS),--forgers $(FORGERS),)

# Game evals: your scorers (evals/scorers/*.py) over runs/practice tasks.
# make eval [RUNS=runs] [SEAT=6] [TEAM=name] [LATEST=N]
eval:
	uv run python -m evals --runs $(or $(RUNS),runs/practice) $(if $(SEAT),--seat $(SEAT),) $(if $(TEAM),--team $(TEAM),) $(if $(LATEST),--latest $(LATEST),)

# Suite files frozen by lock/unlock: make's wildcard keeps empty dirs working.
FROZEN := $(wildcard evals/scorers/*.py) $(wildcard evals/scorers/offline/*.py) evals/gates.toml $(wildcard evals/datasets/*.jsonl)

# Freeze the suite: agents can no longer edit evals/ (evals/.locked).
lock:
	touch evals/.locked
	chmod -R a-w $(FROZEN)
	@echo "evals/ locked: agents are blocked from editing it"

# Unfreeze (humans only; the hook blocks agents from running this).
unlock:
	chmod -R u+w $(FROZEN)
	rm -f evals/.locked
	@echo "evals/ unlocked"

test:
	uv run pytest -q
