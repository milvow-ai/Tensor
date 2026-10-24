"""Which sign-in driver an account uses: by its CLI (``meta.cli``), else by its provider."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from farm.connect.drivers.agy import AgyCheckDriver
from farm.connect.drivers.base import LoginDriver
from farm.connect.drivers.claude import ClaudePasteDriver
from farm.connect.drivers.codex import CodexDeviceDriver


def driver_for(provider_id: str, connection_id: str, meta: Mapping[str, Any]) -> LoginDriver:
    cli = str(meta.get("cli") or provider_id)
    if cli == "codex":
        return CodexDeviceDriver(connection_id, meta)
    if cli == "claude":
        return ClaudePasteDriver(connection_id, meta)
    if cli in ("agy", "gemini"):
        return AgyCheckDriver(connection_id, meta)
    raise NotImplementedError(f"{cli} accounts sign in from the dashboard next (Codex and Claude work now)")
