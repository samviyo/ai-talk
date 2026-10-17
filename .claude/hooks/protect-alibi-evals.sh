#!/usr/bin/env bash
# PreToolUse hook: while alibi/team_evals/.locked exists, block agent writes to the locked eval suite.
# Exit 2 blocks the tool call; the message on stderr is shown to the agent.
set -euo pipefail

payload=$(cat)

root="$(cd "$(dirname "$0")/../.." && pwd)"
locked="$root/alibi/team_evals/.locked"
[ -f "$locked" ] || exit 0

# read -d '' instead of $(): the checker's regexes contain $() and backticks.
read -r -d '' checker <<'PY' || true
import fnmatch, json, os, re, shlex, sys

root = os.path.abspath(sys.argv[1])
try:
    payload = json.loads(sys.stdin.read())
except ValueError:
    print("protect-alibi-evals: unparseable payload", file=sys.stderr)
    sys.exit(2)

tool = payload.get("tool_name", "")
ti = payload.get("tool_input") or {}
cwd = ti.get("cwd") or root

PROTECTED = [
    "alibi/team_evals",
    "alibi/datasets",
    "reference/alibi",
    ".claude/hooks/protect-alibi-evals.sh",
    ".claude/settings.json",
]
PROT_ABS = {os.path.normpath(os.path.join(root, p)) for p in PROTECTED}
PROT_DIRS = [os.path.normpath(os.path.join(root, p)) for p in PROTECTED[:3]]

# Every dir above a protected path (alibi, reference, .claude, repo root, ...) counts too.
ancestors = set()
for p in PROT_ABS:
    q = os.path.dirname(p)
    while q not in ("", os.sep, ancestors):
        ancestors.add(q)
        q = os.path.dirname(q)

# Names glob patterns are matched against: protected paths, their contents, and suffixes.
GLOB_NAMES = set(PROTECTED)
for d in PROT_DIRS:
    if os.path.isdir(d):
        for base, dirs, files in os.walk(d):
            for name in dirs + files:
                GLOB_NAMES.add(os.path.relpath(os.path.join(base, name), root))
MATCH_NAMES = set(GLOB_NAMES)
for n in GLOB_NAMES:
    MATCH_NAMES.add(os.path.normpath(os.path.join(root, n)))
for n in list(MATCH_NAMES):
    parts = n.split("/")
    for i in range(1, len(parts)):
        MATCH_NAMES.add("/".join(parts[i:]))

READ_ONLY = re.compile(r"^(cat|less|head|tail|grep|rg|ls|wc|diff)(\s|$)")
GIT_RO = re.compile(r"^git\s+(diff|log|status)(\s|$)")
MAKE_EVAL = re.compile(r"^make(\s+-\w+)*\s+(eval-alibi|check-alibi)(\s+[\w./:=+-]+)*(\s|$)")
PY_EVAL = re.compile(r"^(uv\s+run\s+)?python3?\s+-m\s+alibi\.(evals|checks)(\s+[\w./:=+-]+)*(\s|$)")
PYTEST = re.compile(r"^(uv\s+run\s+)?pytest(\s+[\w./:=+-]+)*(\s|$)")
SUBST = re.compile(r"\$\(|`|<\(|>\(")
SPICY_ARGS = re.compile(r"(--output|--pre)(=|\s)")
REDIRECT_PROT = re.compile(
    r">>?\s*\S*(" + "|".join(re.escape(p) for p in PROTECTED) + r")(/|$)")


def blocked():
    print("alibi evals are locked (make unlock-alibi to unlock): "
          "teams write the scorers, datasets and gates, not agents.", file=sys.stderr)
    sys.exit(2)


def squash(segment):
    # Quote stripping plus ./ and // collapsing defeats literal-text dodges.
    s = segment.replace("'", "").replace('"', "").replace("\\", "")
    s = re.sub(r"/(\./)+", "/", s)
    return s.replace("//", "/")


def touches_abs(p):
    return (p in PROT_ABS or p in ancestors
            or any(p == d or p.startswith(d + os.sep) for d in PROT_DIRS))


def token_touches(tok):
    if any(c in tok for c in "*?["):
        return any(fnmatch.fnmatch(n, tok) for n in MATCH_NAMES)
    if any(p in tok for p in PROTECTED):
        return True  # e.g. python -c "open('alibi/team_evals/x','w')"
    if tok.startswith("~"):
        tok = os.path.expanduser(tok)
    if os.path.isabs(tok):
        return touches_abs(os.path.normpath(tok))
    return any(touches_abs(os.path.normpath(os.path.join(base, tok)))
               for base in (cwd, root))


def seg_tokens(segment):
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return None  # unbalanced quotes are hostile; treated as touching and not ok


def segment_touches(segment):
    if any(p in squash(segment) for p in PROTECTED):
        return True
    toks = seg_tokens(segment)
    if toks is None:
        return True
    return any(token_touches(t) for t in toks)


def ok(segment):
    segment = segment.strip()
    if SUBST.search(segment):
        return False  # substitution can hide a write behind a read verb
    if SPICY_ARGS.search(segment):
        return False  # e.g. git diff --output=..., rg --pre=...
    if REDIRECT_PROT.search(squash(segment)):
        return False  # redirecting into protected state is a write
    if seg_tokens(segment) is None:
        return False
    return bool(READ_ONLY.match(segment) or GIT_RO.match(segment)
                or MAKE_EVAL.match(segment) or PY_EVAL.match(segment)
                or PYTEST.match(segment))


if tool in ("Edit", "Write", "NotebookEdit"):
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if path:
        if os.path.isabs(path):
            cands = [os.path.normpath(path)]
        else:
            cands = [os.path.normpath(os.path.join(cwd, path)),
                     os.path.normpath(os.path.join(root, path))]
        if any(touches_abs(c) for c in cands):
            blocked()
    sys.exit(0)

if tool == "Bash":
    cmd = ti.get("command", "")
    if not cmd:
        sys.exit(0)
    # unlock-alibi stays blocked in any form, flags and all.
    if re.search(r"unlock-alibi", cmd):
        blocked()
    SEP = re.compile(r"(&&|\|\||;|&|\||\n)")
    parts = [p for p in SEP.split(cmd)]
    segments = [(parts[i].strip(), parts[i - 1] if i > 0 else None)
                for i in range(0, len(parts), 2)]
    for i, (segment, sep) in enumerate(segments):
        if not segment:
            continue
        if segment_touches(segment) and not ok(segment):
            blocked()
        if (sep == "|" and i > 0 and segments[i - 1][0]
                and segment_touches(segments[i - 1][0]) and not ok(segment)):
            blocked()  # pipe carries protected names into a non-read command

sys.exit(0)
PY

rc=0
printf '%s' "$payload" | python3 -c "$checker" "$root" || rc=$?
exit "$rc"
