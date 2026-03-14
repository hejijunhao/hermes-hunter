#!/usr/bin/env bash
set -euo pipefail

# One-command Fly.io deployment for Hermes Prime.
# Usage: ./scripts/deploy-overseer.sh

OVERSEER_APP="hermes-prime-overseer"
HUNTER_APP="hermes-prime-hunter"

# --- Prerequisites ---
if ! command -v fly &>/dev/null; then
    echo "ERROR: 'fly' CLI not found. Install from https://fly.io/docs/flyctl/install/" >&2
    exit 1
fi

if ! fly auth whoami &>/dev/null; then
    echo "ERROR: Not authenticated with Fly.io. Run 'fly auth login' first." >&2
    exit 1
fi

echo "=== Hermes Prime Deployment ==="

# --- Prerequisites: jq ---
if ! command -v jq &>/dev/null; then
    echo "ERROR: 'jq' not found. Install from https://stedolan.github.io/jq/" >&2
    exit 1
fi

# --- Create apps if needed ---
for app in "$OVERSEER_APP" "$HUNTER_APP"; do
    if ! fly apps list --json | jq -e ".[] | select(.Name == \"$app\")" >/dev/null 2>&1; then
        echo "Creating app: $app"
        fly apps create "$app"
    else
        echo "App exists: $app"
    fi
done

# --- Create persistent volume for Overseer ---
if ! fly volumes list --app "$OVERSEER_APP" --json | jq -e '.[] | select(.name == "overseer_data")' >/dev/null 2>&1; then
    echo "Creating volume: overseer_data"
    fly volumes create overseer_data \
        --app "$OVERSEER_APP" \
        --size 10 \
        --region sjc \
        --yes
else
    echo "Volume exists: overseer_data"
fi

# --- Build and push Hunter image ---
echo "Building and pushing Hunter image..."
fly deploy \
    --app "$HUNTER_APP" \
    --config deploy/fly.hunter.toml \
    --build-only \
    --push

HUNTER_IMAGE="registry.fly.io/${HUNTER_APP}:latest"
echo "Hunter image: $HUNTER_IMAGE"

# --- Set Hunter image secret on Overseer ---
fly secrets set \
    --app "$OVERSEER_APP" \
    "HUNTER_FLY_IMAGE=$HUNTER_IMAGE"

# --- Deploy Overseer ---
echo "Deploying Overseer..."
fly deploy \
    --app "$OVERSEER_APP" \
    --config deploy/fly.overseer.toml

# --- Done ---
OVERSEER_URL=$(fly apps list --json | python3 -c "
import json, sys
apps = json.load(sys.stdin)
for a in apps:
    if a.get('Name') == '$OVERSEER_APP':
        print(f\"https://{a['Name']}.fly.dev\")
        break
" 2>/dev/null || echo "https://${OVERSEER_APP}.fly.dev")

echo ""
echo "=== Deployment complete ==="
echo "Overseer: $OVERSEER_URL"
echo ""
echo "Secrets to set (if not already done):"
echo "  fly secrets set --app $OVERSEER_APP \\"
echo "    FLY_API_TOKEN=... \\"
echo "    HUNTER_FLY_APP=$HUNTER_APP \\"
echo "    GITHUB_PAT=... \\"
echo "    HUNTER_REPO=... \\"
echo "    ELEPHANTASM_API_KEY=... \\"
echo "    OPENROUTER_API_KEY=... \\"
echo "    AUTH_PASSWORD=..."
