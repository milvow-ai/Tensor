"""Which sign-in driver an account uses: by its CLI (``meta.cli``), else by its provider."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from farm.connect.drivers.base import LoginDriver
from farm.connect.drivers.claude import ClaudePasteDriver
from farm.connect.drivers.codex import CodexDeviceDriver
from farm.connect.drivers.gemini import GeminiPasteDriver


def driver_for(provider_id: str, connection_id: str, meta: Mapping[str, Any]) -> LoginDriver:
    cli = str(meta.get("cli") or provider_id)
    if cli == "codex":
        return CodexDeviceDriver(connection_id, meta)
    if cli == "claude":
        return ClaudePasteDriver(connection_id, meta)
    if cli == "gemini":
        return GeminiPasteDriver(meta)
    if cli == "agy":
        raise NotImplementedError(
            "Antigravity does not provide isolated dashboard sign-in with verified email discovery. "
            "Import a separately authenticated native profile through advanced registration."
        )
    raise NotImplementedError(f"{cli} accounts sign in from the dashboard next (Codex and Claude work now)")
