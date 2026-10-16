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
  # Normalize so ./, ../ and doubled separators cannot dodge the match.
  norm=$(printf '%s' "$path" | python3 -c 'import os,sys; print(os.path.normpath(os.path.abspath(sys.stdin.read().strip())))')
  case "$norm" in
    */connect4/team_evals|*/connect4/team_evals/*) blocked ;;
  esac
fi

if [ -n "$command" ]; then
  # unlock-c4 stays blocked in any form, flags and all.
  if printf '%s' "$command" | grep -q 'unlock-c4'; then
    blocked
  fi
  # Commands mentioning the team_evals dir must be reads or eval runs, end to end.
  if ! printf '%s' "$command" | python3 -c '
import re, sys

cmd = sys.stdin.read()
# A mention is the path token itself; test_protect_team_evals.py is not one.
MENTION = re.compile(r"(?<![A-Za-z0-9_])team_evals(?![A-Za-z0-9_])")
SEP = re.compile(r"(&&|\|\||;|&|\||\n)")
parts = [p for p in SEP.split(cmd)]
segments = [(parts[i], parts[i - 1] if i > 0 else None)
            for i in range(0, len(parts), 2)]
READ_ONLY = re.compile(r"^(cat|less|head|tail|grep|rg|ls|wc|diff)(\s|$)")
GIT_RO = re.compile(r"^git\s+(diff|log|status)(\s|$)")
MAKE_EVAL = re.compile(r"^make(\s+-\w+)*\s+eval-c4(\s+[\w./:=+-]+)*(\s|$)")
PY_EVAL = re.compile(r"^(uv\s+run\s+)?python3?\s+-m\s+connect4\.evals(\s+[\w./:=+-]+)*(\s|$)")
SUBST = re.compile(r"\$\(|`|<\(|>\(")
SPICY_ARGS = re.compile(r"(--output|--pre)(=|\s)")
REDIRECT_IN = re.compile(r">>?\s*\S*team_evals(/|$)")

def ok(segment):
    segment = segment.strip()
    if SUBST.search(segment):
        return False  # substitution can hide a write behind a read verb
    if SPICY_ARGS.search(segment):
        return False  # e.g. git diff --output=..., rg --pre=...
    if REDIRECT_IN.search(segment):
        return False  # redirecting into team_evals/ is a write
    return bool(READ_ONLY.match(segment) or GIT_RO.match(segment)
                or MAKE_EVAL.match(segment) or PY_EVAL.match(segment))

bad = bool(MENTION.search(cmd)) and REDIRECT_IN.search(cmd)
for i, (segment, sep) in enumerate(segments):
    mentions = bool(MENTION.search(segment))
    if mentions and not ok(segment):
        bad = True
    if sep == "|" and i > 0 and MENTION.search(segments[i - 1][0]) and not ok(segment):
        bad = True  # pipe carries team_evals names into a non-read command
sys.exit(1 if bad else 0)
'; then
    blocked
  fi
fi

exit 0
