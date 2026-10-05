"""Exact results: the worker's final text is kept unmodified, in a file, never in the database.

``FARM_DATA_DIR/ai-results/<job id>.txt`` holds the text byte for byte (UTF-8, written atomically: a reader
sees the whole file or none). The ``ai_jobs`` row keeps only its path, length and SHA-256. A result larger
than ``FARM_AI_RESULT_INLINE_CHARS`` (default 200 000 characters) is returned as that path plus the first
``PREVIEW_CHARS`` characters instead of inline.

Also here: the check of a structured answer against the caller's JSON Schema (errors are reported, the raw
text is always kept) and the ``git status --short`` of an edit's working directory.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from farm.capabilities.schemas import json_schema_errors
from farm.executors.cli_agent.base import parse_json_or_none, run_cli_process
from farm.settings import data_dir

INLINE_ENV = "FARM_AI_RESULT_INLINE_CHARS"
DEFAULT_INLINE_CHARS = 200_000
PREVIEW_CHARS = 20_000
MAX_FILES_LISTED = 200
GIT_TIMEOUT_S = 20.0
_TEXT_ENCODING = "utf-8"
_TEXT_ERRORS = "surrogatepass"  # a lone surrogate in a JSON string must survive the round trip unchanged


def inline_limit() -> int:
    """Characters up to which a result is returned inline (``FARM_AI_RESULT_INLINE_CHARS``, 200 000)."""
    raw = os.environ.get(INLINE_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_INLINE_CHARS
    return value if value >= 1 else DEFAULT_INLINE_CHARS


def results_dir() -> Path:
    return data_dir() / "ai-results"


def result_file(job_id: UUID) -> Path:
    """The file of a job. Its name is the job's UUID, nothing a caller wrote: it cannot leave the folder."""
    return results_dir() / f"{job_id}.txt"


@dataclass(frozen=True)
class StoredResult:
    path: Path
    chars: int
    sha256: str


def store_result(job_id: UUID, text: str) -> StoredResult:
    """Write ``text`` unmodified to the job's result file (blocking: run it in a thread)."""
    path = result_file(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode(_TEXT_ENCODING, errors=_TEXT_ERRORS)
    temp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp.write_bytes(payload)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return StoredResult(path=path, chars=len(text), sha256=hashlib.sha256(payload).hexdigest())


def read_result(path: str | Path) -> str | None:
    """The stored text, or ``None`` when the file is gone (blocking: run it in a thread)."""
    try:
        return Path(path).read_bytes().decode(_TEXT_ENCODING, errors=_TEXT_ERRORS)
    except FileNotFoundError:
        return None


def discard_result(path: str | Path) -> None:
    """Remove a result file whose job did not end up owning it."""
    Path(path).unlink(missing_ok=True)


def preview(text: str) -> str:
    return text[:PREVIEW_CHARS]


@dataclass(frozen=True)
class JsonCheck:
    parsed: Any
    valid: bool
    errors: list[str]


def check_json(text: str, parsed: Any, schema: dict[str, Any]) -> JsonCheck:
    """Parse the worker's answer as JSON (``parsed`` if the executor did) and check it against ``schema``."""
    value = parsed if parsed is not None else parse_json_or_none(text)
    if value is None:
        return JsonCheck(None, False, ["the answer is not valid JSON"])
    errors = json_schema_errors(value, schema)
    return JsonCheck(value, not errors, errors)


async def git_status(cwd: str) -> list[str] | None:
    """``git status --short`` of ``cwd`` (what an edit changed), or ``None`` when it is not a git repository.

    Run through the Farm's CLI runner (argv list, sanitised environment, timeout). ``core.fsmonitor`` is
    switched off because a repository the worker just wrote to could name a program there that
    ``git status`` would run, and ``--no-optional-locks`` keeps the call from touching the index.
    """
    out = await run_cli_process(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "status", "--short"],
        cwd=cwd,
        timeout_s=GIT_TIMEOUT_S,
        max_bytes=256 * 1024,
    )
    if out.timed_out or out.exit_code != 0:
        return None
    lines = [line.rstrip("\r") for line in out.stdout.splitlines() if line.strip()]
    if len(lines) > MAX_FILES_LISTED:
        return [*lines[:MAX_FILES_LISTED], f"... and {len(lines) - MAX_FILES_LISTED} more"]
    return lines
