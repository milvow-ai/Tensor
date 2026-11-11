#!/usr/bin/env bash
# Mirrors scripts/farm-watchdog.ps1: the 24/7 watchdog for Harness Farm, Linux/systemd version.
# One check per run (harness-farm-watchdog.timer, every minute); the unhealthy time is kept between runs.
# systemd already restarts a Farm that exits (Restart=always); this catches a Farm that hangs (no /health) and
# a Bifrost that is down, restarts the unit after UNHEALTHY_THRESHOLD_SECONDS and sends the alert.
set -euo pipefail
# runuser and friends live in sbin; a unit's EnvironmentFile may set a PATH without it.
PATH="${PATH:-/usr/bin:/bin}:/usr/local/sbin:/usr/sbin:/sbin"

FARM_DATA_DIR="${FARM_DATA_DIR:-/var/lib/harness-farm}"
CHECK_INTERVAL_SECONDS="${CHECK_INTERVAL_SECONDS:-60}"
UNHEALTHY_THRESHOLD_SECONDS="${UNHEALTHY_THRESHOLD_SECONDS:-180}"
FARM_BIN="${FARM_BIN:-/opt/harness-farm/.venv/bin/farm}"
FARM_USER="${FARM_USER:-farm}"
FARM_UNIT="${FARM_UNIT:-harness-farm}"
BIFROST_UNIT="${BIFROST_UNIT:-harness-farm-bifrost}"
SKIP_BIFROST="${SKIP_BIFROST:-}"
# Explicit restarts can bypass an automatic-restart policy; retain our own budget across timer invocations.
MAX_RESTARTS="${WATCHDOG_MAX_RESTARTS:-3}"
RESTART_WINDOW_SECONDS="${WATCHDOG_RESTART_WINDOW_SECONDS:-3600}"
[[ "$MAX_RESTARTS" =~ ^[1-9][0-9]*$ && "$RESTART_WINDOW_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
    echo "watchdog restart limits must be positive integers" >&2; exit 2;
}

FARM_HTTP_HOST="${FARM_HTTP_HOST:-127.0.0.1}"
FARM_HTTP_PORT="${FARM_HTTP_PORT:-8787}"
FARM_URL="http://${FARM_HTTP_HOST}:${FARM_HTTP_PORT}"
BIFROST_URL="${BIFROST_URL:-http://127.0.0.1:8080}"

STATE_DIR="${FARM_DATA_DIR}/watchdog"
STATE_FILE="${STATE_DIR}/state"
LOG_FILE="${FARM_DATA_DIR}/logs/watchdog.log"

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")"

log() {
    local line
    line="[$(date -u +"%Y-%m-%dT%H:%M:%SZ")] $1"
    echo "$line"
    echo "$line" >>"$LOG_FILE" 2>/dev/null || true
}

send_alert() {
    local message="$1" kind="${2:-farm_down}"
    log "ALERT: $message"
    local -a cmd=("$FARM_BIN" alert send --kind "$kind" --message "$message" --severity critical)
    # As root (the timer's unit), the alert runs as the Farm's user: it reads the Farm's secrets and writes its
    # data directory, which must never get files owned by root.
    if [[ $EUID -eq 0 ]] && id "$FARM_USER" >/dev/null 2>&1; then
        cmd=(runuser -u "$FARM_USER" -- "${cmd[@]}")
    fi
    local out
    # A broken database/notification provider must not block process recovery indefinitely.
    if ! out=$(timeout --kill-after=5s 20s "${cmd[@]}" 2>&1); then
        log "Failed to dispatch alert via farm alert send: $(printf '%s' "$out" | tail -n 3 | tr '\n' ' ' | cut -c1-400)"
    fi
}

farm_unhealthy=0
bifrost_unhealthy=0
restart_window_started=0
farm_restarts=0
bifrost_restarts=0
farm_restart_blocked=0
bifrost_restart_blocked=0
if [[ -f "$STATE_FILE" ]]; then
    while IFS='=' read -r key value; do
        case "$key" in
            farm_unhealthy) farm_unhealthy="${value//[^0-9]/}" ;;
            bifrost_unhealthy) bifrost_unhealthy="${value//[^0-9]/}" ;;
            restart_window_started) restart_window_started="${value//[^0-9]/}" ;;
            farm_restarts) farm_restarts="${value//[^0-9]/}" ;;
            bifrost_restarts) bifrost_restarts="${value//[^0-9]/}" ;;
            farm_restart_blocked) farm_restart_blocked="${value//[^0-9]/}" ;;
            bifrost_restart_blocked) bifrost_restart_blocked="${value//[^0-9]/}" ;;
        esac
    done <"$STATE_FILE"
fi
farm_unhealthy="${farm_unhealthy:-0}"
bifrost_unhealthy="${bifrost_unhealthy:-0}"
now=$(date -u +"%s")
if (( now < ${restart_window_started:-0} || now - ${restart_window_started:-0} >= RESTART_WINDOW_SECONDS )); then
    restart_window_started="$now"
    farm_restarts=0; bifrost_restarts=0
    farm_restart_blocked=0; bifrost_restart_blocked=0
fi

write_state() {
    printf 'farm_unhealthy=%s\nbifrost_unhealthy=%s\nrestart_window_started=%s\nfarm_restarts=%s\nbifrost_restarts=%s\nfarm_restart_blocked=%s\nbifrost_restart_blocked=%s\n' \
        "$farm_unhealthy" "$bifrost_unhealthy" "$restart_window_started" "${farm_restarts:-0}" \
        "${bifrost_restarts:-0}" "${farm_restart_blocked:-0}" "${bifrost_restart_blocked:-0}" >"$STATE_FILE.tmp"
    mv -f "$STATE_FILE.tmp" "$STATE_FILE"
}

check_bifrost_health() {
    curl -sf --max-time 3 "${BIFROST_URL}/health" >/dev/null 2>&1 ||
        curl -sf --max-time 3 "${BIFROST_URL}/" >/dev/null 2>&1
}

check_farm_health() {
    # Liveness only. A failed /ready dependency must alert, never restart an otherwise responsive process.
    # An independently written heartbeat is not evidence that the HTTP worker or consumer is making progress.
    curl -sf --max-time 3 "${FARM_URL}/health" >/dev/null 2>&1
}

restart_unit() {
    local unit="$1"
    local attempts=0 blocked=0
    if [[ "$unit" == "$FARM_UNIT" ]]; then
        attempts="${farm_restarts:-0}"; blocked="${farm_restart_blocked:-0}"
    else
        attempts="${bifrost_restarts:-0}"; blocked="${bifrost_restart_blocked:-0}"
    fi
    if (( attempts >= MAX_RESTARTS )); then
        if (( blocked == 0 )); then
            if [[ "$unit" == "$FARM_UNIT" ]]; then farm_restart_blocked=1; else bifrost_restart_blocked=1; fi
            write_state
            send_alert "${unit}: restart budget exhausted (${MAX_RESTARTS} in ${RESTART_WINDOW_SECONDS}s); inspect the service and journal. No more watchdog restarts in this window."
        fi
        log "Restart budget exhausted for ${unit}; waiting for operator or next window."
        return
    fi
    if [[ "$unit" == "$FARM_UNIT" ]]; then farm_restarts=$((attempts + 1)); else bifrost_restarts=$((attempts + 1)); fi
    # Persist before the external action, including failed restart attempts.
    write_state
    send_alert "Harness Farm Watchdog: ${unit} failed liveness for >= ${UNHEALTHY_THRESHOLD_SECONDS}s. Restarting (attempt $((attempts + 1))/${MAX_RESTARTS})."
    log "Restarting ${unit}..."
    systemctl restart "$unit" || log "Failed to restart ${unit} via systemctl"
}

log "Harness Farm Watchdog running (Interval=${CHECK_INTERVAL_SECONDS}s, Threshold=${UNHEALTHY_THRESHOLD_SECONDS}s)"

# Bifrost: only where its unit is installed and enabled (a server without Hermes runs no Bifrost).
if [[ -z "$SKIP_BIFROST" ]] && systemctl is-enabled --quiet "$BIFROST_UNIT" 2>/dev/null; then
    if check_bifrost_health; then
        if [[ $bifrost_unhealthy -gt 0 ]]; then log "Bifrost recovered and is now healthy."; fi
        bifrost_unhealthy=0
    else
        bifrost_unhealthy=$((bifrost_unhealthy + CHECK_INTERVAL_SECONDS))
        log "Bifrost unhealthy (${bifrost_unhealthy}s / ${UNHEALTHY_THRESHOLD_SECONDS}s)"
        if [[ $bifrost_unhealthy -ge $UNHEALTHY_THRESHOLD_SECONDS ]]; then
            restart_unit "$BIFROST_UNIT"
            bifrost_unhealthy=0
        fi
    fi
fi

if check_farm_health; then
    if [[ $farm_unhealthy -gt 0 ]]; then log "Farm recovered and is now healthy."; fi
    farm_unhealthy=0
else
    farm_unhealthy=$((farm_unhealthy + CHECK_INTERVAL_SECONDS))
    log "Farm unhealthy (${farm_unhealthy}s / ${UNHEALTHY_THRESHOLD_SECONDS}s)"
    if [[ $farm_unhealthy -ge $UNHEALTHY_THRESHOLD_SECONDS ]]; then
        restart_unit "$FARM_UNIT"
        farm_unhealthy=0
    fi
fi

write_state
exit 0
