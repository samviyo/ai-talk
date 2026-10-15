#!/bin/zsh
# Launch a practice werewolf server with gateway credentials sourced.
# Usage: serve.sh <seed> <port> <runs-dir>
set -e
SEED=$1
PORT=$2
RUNS=$3
cd /Users/sam.mctaggart/Klaviyo/Repos/ai-talk/.claude/worktrees/agent-ab568a0abc8fbbf36
source scripts/gateway-env.sh
if [ -z "$BIFROST_API_KEY" ]; then echo "BIFROST_API_KEY missing" >&2; exit 1; fi
# llm.py appends /v1/chat/completions; gateway-env already ends in /v1, so strip it.
export BIFROST_BASE_URL="${BIFROST_BASE_URL%/v1}"
exec uv run ww serve --tables 1 --window-s 20 --seed "$SEED" --port "$PORT" --runs-dir "$RUNS" --quiet
