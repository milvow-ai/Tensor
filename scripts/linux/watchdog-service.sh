#!/bin/bash
# The privileged watchdog must not execute programs or write files controlled by a worker account.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export WATCHDOG_STATE_DIR=/var/lib/harness-farm-watchdog
export WATCHDOG_LOG_FILE=/var/lib/harness-farm-watchdog/watchdog.log
exec /opt/harness-farm/scripts/linux/farm-watchdog.sh
