#!/usr/bin/env bash
# PreToolUse hook: while connect4/team_evals/.locked exists, block agent edits to it.
# Exit 2 blocks the tool call; the message on stderr is shown to the agent.
set -euo pipefail

payload=$(cat)

root="$(cd "$(dirname "$0")/../.." && pwd)"
locked="$root/connect4/team_evals/.locked"
[ -f "$locked" ] || exit 0

tool=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_name",""))')

path=""
command=""
case "$tool" in
  Edit|Write|NotebookEdit)
    path=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')
    ;;
  Bash)
    command=$(printf '%s' "$payload" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')
    ;;
  *)
    exit 0
    ;;
esac

blocked() {
  echo "connect4/team_evals/ is locked (make unlock-c4 to unlock): teams write the scorers, not agents." >&2
  exit 2
}

if [ -n "$path" ]; then
  case "$path" in
    */connect4/team_evals/*|*/connect4/team_evals) blocked ;;
  esac
fi

if [ -n "$command" ]; then
  # Writing or deleting under team_evals/ via shell is also blocked; reads pass.
  if printf '%s' "$command" | grep -qE '(^|[>|&;[:space:]])(rm|mv|cp|chmod|chown|tee|sed|dd|install|touch|mkdir|rmdir|ln)[[:space:]].*connect4/team_evals/' \
     || printf '%s' "$command" | grep -qE '>+[[:space:]]*[^[:space:]]*connect4/team_evals/' \
     || printf '%s' "$command" | grep -qE 'unlock-c4|team_evals/\.locked|(^|[[:space:];&|])cd[[:space:]]+[^;&|]*team_evals'; then
    blocked
  fi
fi

exit 0
