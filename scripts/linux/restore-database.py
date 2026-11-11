#!/usr/bin/env python3
"""Restore a verified Farm public-schema dump into an explicitly configured empty recovery database."""

import argparse
import os
import sys
from pathlib import Path

from farm.control.backup import restore_public_dump

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("archive", type=Path)
args = parser.parse_args()
try:
    target = os.environ["FARM_RESTORE_DB_URL"]
    restore_public_dump(args.archive, target)
except Exception as exc:
    print(f"Database restore refused or failed ({type(exc).__name__}).", file=sys.stderr)
    sys.exit(2)
print("Farm schema restored. Verify member sign-in and read-only smoke checks before cutover.")
