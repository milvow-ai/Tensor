"""Writer for the owner's registry file (config/registry.yaml).

Maintains the owner's registry file in sync with the database runtime state
so that `farm registry export` and `farm registry sync` round-trip accurately.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from farm.db.pool import DbPool
from farm.registry.models import Registry
from farm.registry.sync import export_registry

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "registry.yaml"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_bytes(text.encode("utf-8"))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


async def write_registry_file(
    pool: DbPool,
    path: Path | str | None = None,
) -> Registry:
    """Export the database registry and write it atomically to the owner's file."""
    if path is None:
        env_path = os.environ.get("FARM_REGISTRY_PATH")
        target = Path(env_path) if env_path else DEFAULT_REGISTRY_PATH
    else:
        target = Path(path)

    registry = await export_registry(pool)
    data: dict[str, Any] = registry.model_dump(mode="json")
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    _atomic_write(target, text)
    return registry


__all__ = ["DEFAULT_REGISTRY_PATH", "write_registry_file"]
