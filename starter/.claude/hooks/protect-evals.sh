#!/usr/bin/env bash
# PreToolUse hook. Unlocked (default): agents may edit evals/ so teams author
# scorers. Locked (evals/.locked exists): agents are blocked from touching
# evals/ at all, and from removing the lock or running `make unlock`.
# Exit 2 blocks the tool call; the message on stderr is shown to the agent.
set -euo pipefail

payload=$(cat)

hook_dir="$(cd "$(dirname "$0")" && pwd)"
starter_dir="$(dirname "$(dirname "$hook_dir")")"
locked=""
[ -e "$starter_dir/evals/.locked" ] && locked=1

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
  echo "evals/ is locked (evals/.locked): the suite is frozen. A human runs \`make unlock\`." >&2
  exit 2
}

if [ -n "$locked" ] && [ -n "$path" ]; then
  case "$path" in
    */evals/*|*/evals|evals/*|evals) blocked ;;
  esac
fi

if [ -n "$command" ] && [ -n "$locked" ]; then
  # While locked, an agent may not write under evals/, remove/move .locked,
  # or run `make unlock`.
  if printf '%s' "$command" | grep -qE '(^|[>|&;[:space:]])(rm|mv|cp|chmod|chown|tee|sed|dd|install|touch|mkdir|rmdir|ln)[[:space:]].*evals/' \
     || printf '%s' "$command" | grep -qE '>+[[:space:]]*[^[:space:]]*evals/' \
     || printf '%s' "$command" | grep -qE '(rm|mv|unlink)[[:space:]].*\.locked' \
     || printf '%s' "$command" | grep -qE 'make[[:space:]]+unlock'; then
    blocked
  fi
fi

exit 0
