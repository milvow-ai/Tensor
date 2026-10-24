#!/usr/bin/env bash
# Mirrors scripts/start-bifrost.ps1: the local Bifrost LLM gateway (127.0.0.1:8080), run by
# harness-farm-bifrost.service as user farm. Its config and virtual keys live in BIFROST_APP_DIR (copied from the
# PC's D:\dev-cache\bifrost, see docs/RUNBOOK-SERVER.md); the OpenRouter key comes from the Farm's secrets file.
set -euo pipefail

FARM_DATA_DIR="${FARM_DATA_DIR:-/var/lib/harness-farm}"
BIFROST_APP_DIR="${BIFROST_APP_DIR:-$FARM_DATA_DIR/bifrost}"
BIFROST_VERSION="${BIFROST_VERSION:-1.6.3}"
export npm_config_cache="${npm_config_cache:-$FARM_DATA_DIR/.npm}"

# The first file that has OPENROUTER_API_KEY: the Farm's secrets, then a Hermes .env (never printed).
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
if [[ -z "$OPENROUTER_API_KEY" ]]; then
    for loc in "${FARM_ENV_FILE:-}" "${HERMES_HOME:-$HOME/.hermes}/.env" "${XDG_CONFIG_HOME:-$HOME/.config}/hermes/.env"; do
        [[ -n "$loc" && -f "$loc" ]] || continue
        while IFS= read -r line; do
            if [[ "$line" =~ ^[[:space:]]*OPENROUTER_API_KEY[[:space:]]*= ]]; then
                OPENROUTER_API_KEY=$(printf '%s' "$line" | sed 's/^[^=]*=[[:space:]]*//' | sed "s/^['\"]//;s/['\"]$//" | tr -d '\r')
                break
            fi
        done <"$loc"
        [[ -n "$OPENROUTER_API_KEY" ]] && break
    done
fi
if [[ -z "$OPENROUTER_API_KEY" ]]; then
    echo "OPENROUTER_API_KEY not found (FARM_ENV_FILE or a Hermes .env)" >&2
    exit 2
fi
export OPENROUTER_API_KEY

mkdir -p "$BIFROST_APP_DIR" "$npm_config_cache"
exec npx -y "@maximhq/bifrost@${BIFROST_VERSION}" \
    -app-dir "$BIFROST_APP_DIR" \
    -port 8080 \
    -host 127.0.0.1 \
    -log-style pretty
