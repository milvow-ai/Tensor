#!/usr/bin/env python3
"""Extract a state archive with Python's data filter, refusing paths/links escaping the target."""
import sys
import tarfile
from pathlib import Path


def extract(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(target.iterdir()) or target.is_symlink():
        raise ValueError("restore target must be an empty regular directory")
    with tarfile.open(archive, "r:") as source:
        source.extractall(target, filter="data")


if __name__ == "__main__":
    try:
        extract(Path(sys.argv[1]), Path(sys.argv[2]))
    except Exception as exc:
        print(f"Safe archive extraction refused ({type(exc).__name__}).", file=sys.stderr)
        sys.exit(2)
