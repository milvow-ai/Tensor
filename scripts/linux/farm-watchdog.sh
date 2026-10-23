#!/usr/bin/env bash
# Mirrors scripts/farm-watchdog.ps1 — 24/7 watchdog for Harness Farm, Linux/systemd version.
set -euo pipefail

# Defaults (can be overridden via environment)
FARM_DATA_DIR="${FARM_DATA_DIR:-/var/lib/harness-farm}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-60}"
UNHEALTHY_THRESHOLD_SECONDS="${UNHEALTHY_THRESHOLD_SECONDS:-180}"
FARM_BIN="${FARM_BIN:-/opt/harness-farm/.venv/bin/farm}"
SKIP_BIFROST="${SKIP_BIFROST:-}"

# HTTP endpoints
FARM_HTTP_HOST="${FARM_HTTP_HOST:-127.0.0.1}"
FARM_HTTP_PORT="${FARM_HTTP_PORT:-8787}"
FARM_URL="http://${FARM_HTTP_HOST}:${FARM_HTTP_PORT}"
BIFROST_URL="${BIFROST_URL:-http://127.0.0.1:8080}"

# Paths
STATE_DIR="${FARM_DATA_DIR}/watchdog"
STATE_FILE="${STATE_DIR}/state"
HEARTBEAT_FILE="${FARM_DATA_DIR}/heartbeats/command_consumer.json"
LOG_DIR="${FARM_DATA_DIR}/logs"
LOG_FILE="${LOG_DIR}/watchdog.log"

# Ensure directories exist
mkdir -p "$STATE_DIR" "$LOG_DIR"

log() {
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local line="[$ts] $1"
    echo "$line"
    echo "$line" >> "$LOG_FILE" 2>/dev/null || true
}

send_alert() {
    local message="$1"
    local kind="${2:-farm_down}"
    log "ALERT: $message"
    "${FARM_BIN}" alert send --kind "$kind" --message "$message" --severity critical 2>/dev/null || {
        log "Failed to dispatch alert via farm alert send"
    }
}

# Read state file, initialize if missing
read_state() {
    if [[ -f "$STATE_FILE" ]]; then
        while IFS='=' read -r key value; do
            case "$key" in
                farm_unhealthy) farm_unhealthy="$value" ;;
                bifrost_unhealthy) bifrost_unhealthy="$value" ;;
            esac
        done < "$STATE_FILE"
    fi
    farm_unhealthy="${farm_unhealthy:-0}"
    bifrost_unhealthy="${bifrost_unhealthy:-0}"
}

# Write state file
write_state() {
    cat > "$STATE_FILE" <<EOF
farm_unhealthy=${farm_unhealthy}
bifrost_unhealthy=${bifrost_unhealthy}
EOF
}

check_bifrost_health() {
    curl -sf --max-time 3 "${BIFROST_URL}/health" >/dev/null 2>&1 || \
    curl -sf --max-time 3 "${BIFROST_URL}/" >/dev/null 2>&1
}

check_farm_health() {
    # 1. Primary check: HTTP /health endpoint
    if curl -sf --max-time 3 "${FARM_URL}/health" >/dev/null 2>&1; then
        return 0
    fi

    # 2. Secondary check: Heartbeat file freshness
    if [[ -f "$HEARTBEAT_FILE" ]]; then
        local raw ts_hb ts_now age
        raw=$(cat "$HEARTBEAT_FILE" 2>/dev/null || echo "{}")
        ts_hb=$(echo "$raw" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('timestamp',''))" 2>/dev/null || echo "")
        if [[ -n "$ts_hb" ]]; then
            ts_now=$(date -u +"%s")
            ts_hb_epoch=$(date -u -d "$ts_hb" +"%s" 2>/dev/null || echo "0")
            if [[ "$ts_hb_epoch" != "0" ]]; then
                age=$((ts_now - ts_hb_epoch))
                if [[ $age -lt $UNHEALTHY_THRESHOLD_SECONDS ]]; then
                    # Check if farm process is running
                    if pgrep -f "farm run" >/dev/null 2>&1; then
                        return 0
                    fi
                fi
            fi
        fi
    fi
    return 1
}

restart_bifrost() {
    log "Restarting Bifrost..."
    send_alert "Harness Farm Watchdog: Bifrost gateway unhealthy for >= ${UNHEALTHY_THRESHOLD_SECONDS}s. Restarting." "farm_down"
    systemctl restart bifrost 2>/dev/null || {
        log "Failed to restart bifrost via systemctl"
    }
}

restart_farm() {
    log "Restarting Farm..."
    send_alert "Harness Farm Watchdog: Farm process unhealthy (/health failed). Restarting." "farm_down"
    systemctl restart harness-farm 2>/dev/null || {
        log "Failed to restart harness-farm via systemctl"
    }
}

# Load persisted counters
read_state

log "Harness Farm Watchdog running (Interval=${CHECK_INTERVAL_SECONDS}s, Threshold=${UNHEALTHY_THRESHOLD_SECONDS}s)"

# Check Bifrost
if [[ -z "$SKIP_BIFROST" ]]; then
    if check_bifrost_health; then
        if [[ $bifrost_unhealthy -gt 0 ]]; then
            log "Bifrost recovered and is now healthy."
        fi
        bifrost_unhealthy=0
    else
        ((bifrost_unhealthy += CHECK_INTERVAL_SECONDS)) || true
        log "Bifrost unhealthy (${bifrost_unhealthy}s / ${UNHEALTHY_THRESHOLD_SECONDS}s)"
        if [[ $bifrost_unhealthy -ge $UNHEALTHY_THRESHOLD_SECONDS ]]; then
            restart_bifrost
            bifrost_unhealthy=0
        fi
    fi
fi

# Check Farm
if check_farm_health; then
    if [[ $farm_unhealthy -gt 0 ]]; then
        log "Farm recovered and is now healthy."
    fi
    farm_unhealthy=0
else
    ((farm_unhealthy += CHECK_INTERVAL_SECONDS)) || true
    log "Farm unhealthy (${farm_unhealthy}s / ${UNHEALTHY_THRESHOLD_SECONDS}s)"
    if [[ $farm_unhealthy -ge $UNHEALTHY_THRESHOLD_SECONDS ]]; then
        restart_farm
        farm_unhealthy=0
    fi
fi

# Persist counters for next run
write_state

exit 0
