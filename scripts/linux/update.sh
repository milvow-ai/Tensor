#!/usr/bin/env bash
# Update the server to the newest commit of its branch (run as root):
#   backup-state.sh -> git fast-forward -> uv sync -> farm db backup -> farm db migrate -> restart -> /health.
# If the new code does not answer /health within 90 s, the old commit is put back and restarted (migrations
# are expand-only, so the old code runs on the new schema) and the alert is sent.
#   update.sh [--branch NAME] [--skip-state-backup]
set -euo pipefail

APP="${FARM_APP_DIR:-/opt/harness-farm}"
SETTINGS="${FARM_SETTINGS_FILE:-/etc/harness-farm/farm.env}"
FARM_UNIT="${FARM_UNIT:-harness-farm}"
# The port the service listens on is the one in its settings file, not whatever this shell has.
port=$(sed -n 's/^FARM_HTTP_PORT=//p' "$SETTINGS" 2>/dev/null | tail -n 1)
FARM_HTTP_PORT="${port:-8787}"
UV="${UV_BIN:-uv}"
FARM="${FARM_WRAPPER:-/usr/local/bin/farm}"
branch="" state_backup=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch) branch="$2"; shift 2 ;;
        --skip-state-backup) state_backup=0; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
done
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 2; }
branch="${branch:-$(git -C "$APP" rev-parse --abbrev-ref HEAD)}"

healthy() {
    local i
    for i in $(seq 1 45); do
        curl -sf --max-time 2 "http://127.0.0.1:${FARM_HTTP_PORT}/health" >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

if [[ $state_backup -eq 1 ]]; then "$APP/scripts/linux/backup-state.sh"; fi

old=$(git -C "$APP" rev-parse HEAD)
git -C "$APP" fetch --quiet origin "$branch"
if ! git -C "$APP" merge --ff-only --quiet FETCH_HEAD; then
    echo "$APP has local changes or diverged from origin/$branch: not updated" >&2
    exit 1
fi
new=$(git -C "$APP" rev-parse HEAD)
echo "code: ${old:0:9} -> ${new:0:9} ($branch)"
(cd "$APP" && UV_PYTHON_DOWNLOADS=never "$UV" sync --frozen --no-dev --python /usr/bin/python3.12 --quiet)
chmod -R a+rX "$APP"

"$FARM" db backup
"$FARM" db migrate
systemctl restart "$FARM_UNIT"
if healthy; then
    echo "update done: ${new:0:9} is healthy"
    exit 0
fi

echo "the new code did not become healthy: back to ${old:0:9}" >&2
git -C "$APP" reset --quiet --hard "$old"
(cd "$APP" && UV_PYTHON_DOWNLOADS=never "$UV" sync --frozen --no-dev --python /usr/bin/python3.12 --quiet)
systemctl restart "$FARM_UNIT"
"$FARM" alert send --kind farm_down --severity critical \
    --message "Harness Farm update to ${new:0:9} failed its health check; rolled back to ${old:0:9}" || true
exit 1
