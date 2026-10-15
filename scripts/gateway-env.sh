# Source to point bots at the developer gateway with your personal key (never printed).
# Bare origin: the server appends /v1/chat/completions; connect4 bots do too (see below).
export BIFROST_BASE_URL=https://developer-gateway.services.clovesoftware-dev.com
export BIFROST_API_KEY=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.pi/agent/models.json')))['providers']['klaviyo-bifrost']['headers']['x-bf-vk'])")
