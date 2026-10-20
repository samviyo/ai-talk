# Source to point bots at the Bifrost dev workspace (virtual key "smarter-translations").
# The key comes from BIFROST_VIRTUAL_KEY in ~/.zshenv; nothing secret lives in this file.
export BIFROST_BASE_URL=https://bifrost.services.clovesoftware-dev.com
export BIFROST_API_KEY="$BIFROST_VIRTUAL_KEY"
# Without the organiser key, fall back to the team key make setup saved.
if [ -z "$BIFROST_API_KEY" ] && [ -f "$(dirname "${BASH_SOURCE[0]:-$0}")/../.bifrost.env" ]; then
  set -a; . "$(dirname "${BASH_SOURCE[0]:-$0}")/../.bifrost.env"; set +a
fi
