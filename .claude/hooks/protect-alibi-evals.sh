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
import fnmatch, glob, json, os, re, shlex, sys

root = os.path.abspath(sys.argv[1])
try:
    payload = json.loads(sys.stdin.read())
except ValueError:
    print("protect-alibi-evals: unparseable payload", file=sys.stderr)
    sys.exit(2)

tool = payload.get("tool_name", "")
ti = payload.get("tool_input") or {}
cwd = ti.get("cwd") or root

PROTECTED_DIRS = [
    "alibi/team_evals",
    "alibi/datasets",
    "reference/alibi",
    "alibi/golden",
    "alibi/migrate",
]
# The Python tool is the migration's source of truth, so it freezes with the evals.
PROTECTED = PROTECTED_DIRS + [
    "alibi/checks.py",
    "alibi/scorers.py",
    ".claude/hooks/protect-alibi-evals.sh",
    ".claude/settings.json",
]
PROT_ABS = {os.path.normpath(os.path.join(root, p)) for p in PROTECTED}
PROT_DIRS = [os.path.normpath(os.path.join(root, p)) for p in PROTECTED_DIRS]

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
# Line-range printing only: sed's w command and -i both write files.
SED_RO = re.compile(r"""^sed\s+-n\s+'?\d+(,(\d+|\$))?p'?(\s+[^\s'"-][^\s'"]*)+\s*$""")
GIT_RO = re.compile(r"^git\s+(diff|log|status|show)(\s|$)")
ENV = r"^(\w+=\S*\s+)*"  # e.g. ALIBI_HELDOUT_SEED=$seed make eval-alibi
MAKE_EVAL = re.compile(ENV + r"make(\s+-\w+)*\s+(eval-alibi|check-alibi)(\s+[\w./:=+-]+)*(\s|$)")
# Running the port on golden inputs reads them; only the binary's stdout is written.
PORT_RUN = re.compile(r"^(\./)?alibi/zig/zig-out/bin/alibi-check(\s+[\w./-]+)*\s*$")
PY_EVAL = re.compile(ENV + r"(uv\s+run\s+)?python3?\s+-m\s+alibi\.(evals|checks)(\s+[\w./:=+-]+)*(\s|$)")
# Allowlisted flags only: --basetemp, --junitxml, -p, -c, -o and friends can write paths.
PYTEST = re.compile(ENV + r"(uv\s+run\s+)?(python3?\s+-m\s+)?pytest"
                    r"(\s+(-q|-v|-vv|-x|-s|--lf|--ff|[\w./:\[\]][\w./:\[\]-]*))*\s*$")
SUBST = re.compile(r"\$\(|`|<\(|>\(")
SPICY_ARGS = re.compile(r"(--output|--pre)(=|\s)")
REDIRECT = re.compile(r"\d*&?>>?\|?\s*([^\s;&|<>]+)")


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
    if "=" in tok and token_touches(tok.split("=", 1)[1]):
        return True  # X=alibi or --dir=alibi can carry a protected path into a later $X
    if any(c in tok for c in "*?["):
        if any(fnmatch.fnmatch(n, tok) for n in MATCH_NAMES):
            return True
        # Expand against the real tree too: .clau?e names no protected path but matches .claude.
        return any(touches_abs(os.path.normpath(m))
                   for base in (cwd, root)
                   for m in glob.glob(os.path.join(base, os.path.expanduser(tok))))
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
    if "$" in segment:
        return True  # a variable can expand to any path, so only reads may use one
    if any(token_touches(t.strip("'\"")) for t in REDIRECT.findall(segment)):
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
    targets = REDIRECT.findall(segment)
    if any(token_touches(t.strip("'\"")) for t in targets):
        return False  # redirecting into protected state is a write, however the path is spelled
    if any(c in t for t in targets for c in "$*?["):
        return False  # an expanded redirect target can't be resolved here, so it is never a read
    if seg_tokens(segment) is None:
        return False
    return bool(READ_ONLY.match(segment) or SED_RO.match(segment) or GIT_RO.match(segment)
                or MAKE_EVAL.match(segment) or PY_EVAL.match(segment) or PORT_RUN.match(segment)
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
    # unlock-alibi and unlock-golden stay blocked in any form, flags and all.
    if re.search(r"unlock-(alibi|golden)", cmd):
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
