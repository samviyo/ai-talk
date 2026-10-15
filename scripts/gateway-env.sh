# Source to point bots at the developer gateway with your personal key (never printed).
export BIFROST_BASE_URL=https://developer-gateway.services.clovesoftware-dev.com/v1
export BIFROST_API_KEY=$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.pi/agent/models.json')))['providers']['klaviyo-bifrost']['headers']['x-bf-vk'])")
