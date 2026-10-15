#!/usr/bin/env bash
# PreToolUse hook: block Edit/Write/Bash that would modify evals/.
# Exit 2 blocks the tool call; the message on stderr is shown to the agent.
set -euo pipefail

payload=$(cat)

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
  echo "evals/ is read-only for agents: improve the bot, not the checks." >&2
  exit 2
}

if [ -n "$path" ]; then
  case "$path" in
    */evals/*|*/evals|evals/*|evals) blocked ;;
  esac
fi

if [ -n "$command" ]; then
  # Writing or deleting under evals/ via shell is also blocked; reads pass.
  if printf '%s' "$command" | grep -qE '(^|[>|&;[:space:]])(rm|mv|cp|chmod|chown|tee|sed|dd|install|touch|mkdir|rmdir|ln)[[:space:]].*evals/' \
     || printf '%s' "$command" | grep -qE '>+[[:space:]]*[^[:space:]]*evals/'; then
    blocked
  fi
fi

exit 0
