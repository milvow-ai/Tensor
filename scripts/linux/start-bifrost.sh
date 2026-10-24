#!/usr/bin/env bash
# Mirrors scripts/start-bifrost.ps1 — start the local Bifrost LLM gateway on Linux.
set -euo pipefail

# Read OPENROUTER_API_KEY from Hermes env file or EnvironmentFile
# On Linux: check XDG_CONFIG_HOME first, then ~/.config, then /etc
HERMES_ENV=""
for loc in "${XDG_CONFIG_HOME:-$HOME/.config}/hermes/.env" "$HOME/.config/hermes/.env" "/etc/harness-farm/hermes.env" "/etc/harness-farm/farm.env"; do
    if [[ -f "$loc" ]]; then
        HERMES_ENV="$loc"
        break
    fi
done

if [[ -z "$HERMES_ENV" ]]; then
    echo "OPENROUTER_API_KEY not found: no hermes .env located" >&2
    exit 2
fi

# Extract OPENROUTER_API_KEY (never print it)
OPENROUTER_API_KEY=""
while IFS= read -r line; do
    if [[ "$line" =~ ^[[:space:]]*OPENROUTER_API_KEY[[:space:]]*= ]]; then
        OPENROUTER_API_KEY=$(echo "$line" | sed 's/^[^=]*=[[:space:]]*//' | sed "s/^['\"]//;s/['\"]$//" | tr -d '\r')
        break
    fi
done < "$HERMES_ENV"

if [[ -z "$OPENROUTER_API_KEY" ]]; then
    echo "OPENROUTER_API_KEY not found in $HERMES_ENV" >&2
    exit 2
fi

export OPENROUTER_API_KEY
export npm_config_cache="${npm_config_cache:-/var/cache/npm}"

# Bifrost data directory
BIFROST_APP_DIR="${BIFROST_APP_DIR:-/var/lib/harness-farm/bifrost}"
mkdir -p "$BIFROST_APP_DIR"

npx -y @maximhq/bifrost \
    -app-dir "$BIFROST_APP_DIR" \
    -port 8080 \
    -host 127.0.0.1 \
    -log-style pretty \
    >> "$BIFROST_APP_DIR/server.log" 2>&1
