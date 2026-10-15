#!/bin/zsh
# Run N games by chaining one.sh (fresh server per game). Verbose server keeps
# the process group alive under the task monitor; a fresh server per game
# sidesteps the auto-restart persistence drop (each game is always game1 of a
# new server, so nothing is overwritten or skipped).
# Usage: runiter.sh <base-seed> <base-port> <runs-dir> <n-games>
set -e
SEED=$1; PORT=$2; RUNS=$3; N=${4:-6}
WT=/Users/sam.mctaggart/Klaviyo/Repos/ai-talk/.claude/worktrees/agent-ab568a0abc8fbbf36
rm -rf "$RUNS"; mkdir -p "$RUNS/table1"
for n in $(seq 1 "$N"); do
  sub="$RUNS/_g$n"
  "$WT/dogfood/team_b/one.sh" "$((SEED+n))" "$PORT" "$sub" >/dev/null 2>&1 || true
  # fold this game's table1/game1 into the series as gameN
  if [ -f "$sub/table1/game1/game.json" ]; then
    cp -R "$sub/table1/game1" "$RUNS/table1/game$n"
    echo "game $n recorded"
  else
    echo "game $n NOT recorded"
  fi
  cat "$sub/bot.log" >> "$RUNS/bot.log" 2>/dev/null || true
  rm -rf "$sub"
done
echo "done: $(ls $RUNS/table1 | tr '\n' ' ')"
