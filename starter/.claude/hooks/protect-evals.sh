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

if [ -z "$locked" ]; then
  exit 0
fi

# The locked-state decision lives in Python: per-segment Bash parsing beats grep here.
verdict=$(python3 - "$payload" <<'PY'
import json
import os
import re
import sys

READ_ONLY = {"cat", "less", "head", "tail", "grep", "rg", "ls", "wc", "diff"}
GIT_READ_ONLY = {"diff", "log", "status"}
MAKE_TARGETS = {"eval", "check", "practice"}
MAKE_VALUE_FLAGS = {"-C", "-f", "-I", "-o", "-W", "-j",
                    "--directory", "--file", "--jobs", "--old-file"}
PREFIX = {"sudo", "uv", "run", "env", "nice", "nohup", "time", "timeout",
          "xargs", "watch"}


def python_evals_allowed(seg):
    toks = seg.split()
    i = 0
    while i < len(toks) and (
        toks[i] in PREFIX
        or toks[i].isdigit()
        or re.fullmatch(r"[A-Za-z_]\w*=\S*", toks[i])
        or toks[i].startswith("-")
    ):
        i += 1
    rest = toks[i:]
    if len(rest) < 3 or rest[0] not in ("python", "python3"):
        return False
    if "-m" not in rest[1:-1]:
        return False
    m = rest.index("-m")
    module_ok = rest[m + 1] in ("evals", "evals.offline")
    return module_ok and all(t.startswith("-") for t in rest[1:m])


def make_allowed(seg):
    toks = seg.split()
    targets = []
    skip_value = False
    for t in toks[1:]:
        if skip_value:
            skip_value = False
            continue
        if t.startswith("-"):
            if t in MAKE_VALUE_FLAGS:
                skip_value = True
            continue
        if "=" in t:
            continue
        targets.append(t)
    return all(t in MAKE_TARGETS for t in targets)


def segment_allowed(seg):
    toks = seg.split()
    if not toks:
        return True
    if toks[0] in READ_ONLY:
        return True
    if toks[0] == "git" and len(toks) > 1 and toks[1] in GIT_READ_ONLY:
        return True
    if toks[0] == "make":
        return make_allowed(seg)
    return python_evals_allowed(seg)


def decide(payload):
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input") or {}
    if tool in ("Edit", "Write", "NotebookEdit"):
        path = os.path.normpath(os.path.abspath(inp.get("file_path", "")))
        return bool(re.search(r"(^|/)evals(/|$)", path))
    if tool == "Bash":
        cmd = inp.get("command", "")
        if re.search(r"\bunlock\b", cmd):
            return True
        if re.search(r"\b(rm|mv|unlink)\b.*\.locked", cmd):
            return True
        if re.search(r"\bevals\b", cmd):
            return any(re.search(r"\bevals\b", seg) and not segment_allowed(seg)
                       for seg in re.split(r"[|;&\n]+", cmd))
    return False


print("blocked" if decide(json.loads(sys.argv[1])) else "ok")
PY
)

if [ "$verdict" = "blocked" ]; then
  echo "evals/ is locked (evals/.locked): the suite is frozen. A human runs \`make unlock\`." >&2
  exit 2
fi

exit 0
