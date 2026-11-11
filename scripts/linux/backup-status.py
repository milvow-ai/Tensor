#!/usr/bin/env python3
"""Write success only after encryption, checksum and configured off-host delivery completed."""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

state = Path(os.environ.get("FARM_DATA_DIR", "/var/lib/harness-farm")) / "backups"
state.mkdir(mode=0o700, parents=True, exist_ok=True)
success = sys.argv[1] == "success"
value = {
    "completed_at": datetime.now(UTC).isoformat(), "success": success,
    "archive": Path(sys.argv[2]).name if success else None,
    "database_included": os.environ.get("FARM_BACKUP_DATABASE", "1") == "1",
    "offsite": bool(os.environ.get("FARM_BACKUP_REMOTE")),
}
for filename in (["state-success.json", "state-attempt.json"] if success else ["state-attempt.json"]):
    status = state / filename
    temporary = status.with_suffix(".part")
    with temporary.open("w", encoding="utf-8") as out:
        os.chmod(temporary, 0o600)
        json.dump(value, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, status)
