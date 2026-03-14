#!/usr/bin/env bash
set -euo pipefail

# Overseer entrypoint: runs ttyd (foreground) + OverseerLoop (background)
# with graceful shutdown on SIGTERM/SIGINT.

# --- State directories on persistent volume ---
mkdir -p /data/hermes/hunter/{logs,injections}
mkdir -p /data/hunter-repo

# --- Git config (for commit operations) ---
git config --global user.name "Hermes Overseer"
git config --global user.email "overseer@hermes-prime"

# --- OverseerLoop in background ---
OVERSEER_INTERVAL="${OVERSEER_INTERVAL:-300}"
hermes hunter overseer --interval "$OVERSEER_INTERVAL" &
OVERSEER_PID=$!

# --- Signal handling ---
cleanup() {
    echo "[entrypoint] Shutting down..."
    kill "$OVERSEER_PID" 2>/dev/null || true
    wait "$OVERSEER_PID" 2>/dev/null || true
    exit 0
}
trap cleanup SIGTERM SIGINT

# --- ttyd (PID 1 via exec) ---
TTYD_ARGS=("--port" "8080" "--writable")

if [ -n "${AUTH_PASSWORD:-}" ]; then
    # Write credentials to a temp file so the password isn't visible in `ps aux`.
    CRED_FILE=$(mktemp)
    echo "hermes:${AUTH_PASSWORD}" > "$CRED_FILE"
    chmod 600 "$CRED_FILE"
    TTYD_ARGS+=("--credential" "file:${CRED_FILE}")
fi

exec ttyd "${TTYD_ARGS[@]}" bash
