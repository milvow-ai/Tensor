#!/usr/bin/env python3
"""Dump the Farm schema for the encrypted archive, excluding Supabase internals."""
import os
import shutil
import sys
from pathlib import Path

from farm.control.backup import create_backup
from farm.settings import load_env

if __name__ == "__main__":
    try:
        load_env()
        if not (os.environ.get("FARM_DB_URL") or os.environ.get("SUPABASE_DB_URL")):
            raise ValueError("explicit database configuration required")
        path = create_backup(schema="public")
        if path.stat().st_size == 0:
            raise ValueError("empty database dump")
        destination = Path(sys.argv[1])
        destination.mkdir(parents=True, mode=0o700)
        shutil.copyfile(path, destination / "database.dump")
        (destination / "database.dump").chmod(0o600)
    except Exception as exc:
        print(f"Database archive backup failed ({type(exc).__name__}).", file=sys.stderr)
        sys.exit(2)
