#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
unset FARM_DB_URL SUPABASE_DB_URL SUPABASE_SERVICE_ROLE_KEY
exec /usr/bin/node /opt/harness-farm/console/node_modules/next/dist/bin/next start \
    --hostname 127.0.0.1 --port "${FARM_CONSOLE_PORT:?console port is required}"
