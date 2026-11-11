"""Private durable terminal-write spool. Entries never describe work to execute."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from farm.settings import data_dir

MAX_METADATA_BYTES = 1_048_576
MAX_REPLAY_BATCH = 100


def root() -> Path:
    return data_dir() / "ai-completions"


def sync_directory(path: Path) -> None:
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def write(tenant_id: UUID, job_id: UUID, payload: bytes) -> Path:
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError("terminal metadata exceeds spool limit")
    folder = root() / str(tenant_id)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        root().chmod(0o700)
        folder.chmod(0o700)
    path = folder / f"{job_id}.json"
    wrapped = json.dumps(
        {"sha256": hashlib.sha256(payload).hexdigest(), "payload": payload.decode("utf-8")},
        ensure_ascii=True,
    ).encode("utf-8")
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    try:
        with open(os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
            stream.write(wrapped)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(folder)
        sync_directory(root())
    finally:
        temporary.unlink(missing_ok=True)
    return path


def entries() -> list[Path]:
    base = root()
    if not base.exists():
        return []
    found: list[Path] = []
    for folder in base.iterdir():
        if folder.is_symlink() or not folder.is_dir():
            continue
        try:
            UUID(folder.name)
        except ValueError:
            continue
        for path in folder.glob("*.json"):
            found.append(path)
            if len(found) >= MAX_REPLAY_BATCH:
                return found
    return found


def read(path: Path) -> bytes:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("symlink in terminal spool")
    if path.stat().st_size > MAX_METADATA_BYTES * 8:
        raise ValueError("terminal envelope exceeds spool limit")
    UUID(path.parent.name)
    UUID(path.stem)
    wrapped = json.loads(path.read_bytes())
    if not isinstance(wrapped, dict) or not isinstance(wrapped.get("payload"), str):
        raise ValueError("invalid terminal envelope")
    payload: bytes = wrapped["payload"].encode("utf-8")
    if len(payload) > MAX_METADATA_BYTES or hashlib.sha256(payload).hexdigest() != wrapped["sha256"]:
        raise ValueError("terminal spool checksum mismatch")
    return payload


def discard(path: Path) -> None:
    path.unlink(missing_ok=True)
    sync_directory(path.parent)


def quarantine(path: Path, reason: str) -> None:
    """Retain evidence outside the replay set, without exposing its contents in logs."""
    os.replace(path, path.with_suffix(f".{reason}"))
    sync_directory(path.parent)
