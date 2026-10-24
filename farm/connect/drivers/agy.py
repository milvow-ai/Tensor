"""Antigravity (agy) accounts: a check, not a sign-in, for now.

agy has no sign-in command; it signs in on first use. On Windows its Google login is per Windows user, not per
home folder (a fresh ``HOME``/``USERPROFILE`` still answers with the machine's login), so a pool of agy
accounts lives as separate Linux users (WSL now, the server later: ``meta.cli_prefix``). What the dashboard
can do today is run the account once and show the truth: it answers (ready), or Google asks to verify the
account (a link to open), or it is not usable (the reason). The staging home is not used; nothing is promoted.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from farm.connect.drivers.base import Failure, Identity, NeedsVerification, Success
from farm.connect.drivers.process import child_env, resolve_cli, run_once
from farm.connect.service import CheckResult
from farm.secrets import redact

VERIFY = re.compile(r"https://accounts\.google\.com/\S+")
PROMPT = "Respond with only the word PONG"


class AgyCheckDriver:
    method = "loopback"

    def __init__(
        self, connection_id: str, meta: Mapping[str, Any], *, command: Sequence[str] | None = None
    ) -> None:
        self._connection_id = connection_id
        prefix = meta.get("cli_prefix")
        self._prefix = [str(part) for part in prefix] if isinstance(prefix, list) else []
        self._user = next((self._prefix[i + 1] for i, p in enumerate(self._prefix[:-1]) if p == "-u"), None)
        self._command = list(command) if command else None
        self._status = -1
        self._out = ""

    async def start(self, home: Path, hint: str | None = None) -> None:
        argv = self._command or (self._prefix + ["agy"] if self._prefix else resolve_cli("agy"))
        self._status, self._out = await run_once(
            [*argv, "-p", PROMPT, "--print-timeout", "60s"], child_env({}), None, 120
        )

    async def events(self) -> AsyncIterator[Any]:
        link = VERIFY.search(self._out)
        if "PONG" in self._out.upper() and self._status == 0:
            yield Success()
        elif link:
            message = "Google asks you to verify this account first."
            yield NeedsVerification(url=link.group(0), message=message)
        else:
            excerpt = redact(self._out.strip()[-400:]) or f"exit code {self._status}"
            yield Failure(kind="not_usable", excerpt=excerpt)

    async def submit(self, text: str) -> None:
        raise RuntimeError("an agy check takes no pasted code")

    async def cancel(self) -> None:
        return None

    async def identity(self, home: Path) -> Identity | None:
        # The Google account is not readable here; the account's Linux user (or the machine login) names it.
        who = f"wsl:{self._user}" if self._user else f"windows:{self._connection_id}"
        return Identity(provider_account_id=who, email="", plan=None, org=None)

    async def models(self, home: Path) -> list[str]:
        return []

    async def test_login(self, home: Path) -> CheckResult:
        return CheckResult(ok=self._status == 0 and "PONG" in self._out.upper())

    def promote(self, staging: Path, home: Path) -> None:
        return None  # nothing to swap: the check changed no login
