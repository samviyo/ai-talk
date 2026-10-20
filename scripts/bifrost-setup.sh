#!/usr/bin/env bash
# Called by make setup: asks for the team's Bifrost key once, saves it to .bifrost.env and checks it.
# The key is never echoed. An exported BIFROST_API_KEY skips the prompt.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE=.bifrost.env
BASE=https://bifrost.services.clovesoftware-dev.com

key="${BIFROST_API_KEY:-}"
from_file=
if [ -z "$key" ] && [ -f "$FILE" ]; then
  key="$(sed -n 's/^BIFROST_API_KEY=//p' "$FILE")"; from_file=1
fi
if [ -z "$key" ]; then
  if [ ! -t 0 ]; then
    echo "bifrost: no key saved and no terminal to ask; run make setup in a terminal"; exit 1
  fi
  read -rsp "Paste your Bifrost key (from the 1Password link), then Enter: " key; echo
  key="$(printf '%s' "$key" | tr -d '[:space:]')"
  [ -n "$key" ] || { echo "bifrost: no key entered"; exit 1; }
fi

# Bifrost dev only enforces x-bf-vk, and /v1/models checks it without spending anything.
code="$(curl -s -o /dev/null -w '%{http_code}' -H "x-bf-vk: $key" "$BASE/v1/models" || true)"
if [ "$code" != 200 ]; then
  [ -z "$from_file" ] || rm -f "$FILE"
  echo "bifrost: key rejected (HTTP $code). Check you copied the whole key, then run make setup again."; exit 1
fi

umask 077
printf 'BIFROST_BASE_URL=%s\nBIFROST_API_KEY=%s\n' "$BASE" "$key" > "$FILE"
echo "bifrost: key works, saved to $FILE"
