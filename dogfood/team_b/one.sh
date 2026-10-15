#!/bin/zsh
# One game, verbose server, live logs. Usage: one.sh <seed> <port> <runs-dir>
set -e
SEED=$1; PORT=$2; RUNS=$3
WT=/Users/sam.mctaggart/Klaviyo/Repos/ai-talk/.claude/worktrees/agent-ab568a0abc8fbbf36
cd "$WT"
source scripts/gateway-env.sh
export BIFROST_BASE_URL="${BIFROST_BASE_URL%/v1}"
rm -rf "$RUNS"; mkdir -p "$RUNS"
# verbose server (no --quiet) so we see startup + any traceback
uv run ww serve --tables 1 --window-s 20 --seed "$SEED" --port "$PORT" --runs-dir "$RUNS" \
  > "$RUNS/server.log" 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
for i in $(seq 1 30); do [ -f "$RUNS/tokens.json" ] && break; sleep 1; done
TOKEN=$(python3 -c "import json;print(json.load(open('$WT/$RUNS/tokens.json'))['tables']['table1'][6])")
( cd "$WT/dogfood/team_b" && env -i PATH="$PATH" HOME="$HOME" \
    WW_SERVER="http://127.0.0.1:$PORT" WW_TOKEN="$TOKEN" uv run bot ) > "$RUNS/bot.log" 2>&1 &
BOTPID=$!
timeout 400 uv run ww table --server "http://127.0.0.1:$PORT" --table 1 \
  --bots baseline,baseline,accuser,quiet,random,baseline --runs-dir "$RUNS" \
  > "$RUNS/table.log" 2>&1 || true
for i in $(seq 1 12); do kill -0 $BOTPID 2>/dev/null || break; sleep 1; done
kill $BOTPID 2>/dev/null || true
echo "recorded: $(ls $RUNS/table1 2>/dev/null | tr '\n' ' ')"
