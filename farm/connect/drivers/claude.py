"""Claude (subscription) accounts sign in on claude.com and paste back the code the page then shows.

``claude auth login [--email hint]`` prints the sign-in link and reads the code from stdin ("Paste code here
if prompted >"), then writes ``.credentials.json`` (and the account in ``.claude.json``) into
``CLAUDE_CONFIG_DIR``.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from farm.connect.drivers.base import Failure, Identity, NeedsInput, Success, Url
from farm.connect.drivers.process import CliProcess, child_env, replace_files, resolve_cli, run_once
from farm.connect.homes import promote as swap_home
from farm.connect.service import CheckResult
from farm.secrets import redact

AUTHORIZE = re.compile(r"https://\S*oauth/authorize\S*")
TEST_PROMPT = "Respond with only the word PONG"
PASTE_HINT = "After you sign in and approve, the page shows a code. Paste it here."
WSL_LATER = "This Claude account runs inside WSL; it signs in from the dashboard after the server move."
MODELS = ["opus", "sonnet", "haiku"]  # Claude Code's model aliases (every subscription plan)


class ClaudePasteDriver:
    method = "paste_code"

    def __init__(
        self, connection_id: str, meta: Mapping[str, Any], *, command: Sequence[str] | None = None
    ) -> None:
        if meta.get("cli_prefix"):
            raise NotImplementedError(WSL_LATER)
        self._connection_id = connection_id
        self._command = list(command) if command else None
        self._proc: CliProcess | None = None
        self._home: Path | None = None

    def _cli(self) -> list[str]:
        return self._command or resolve_cli("claude")

    async def start(self, home: Path, hint: str | None = None) -> None:
        home.mkdir(parents=True, exist_ok=True)
        self._home = home
        argv = [*self._cli(), "auth", "login", *(["--email", hint] if hint else [])]
        self._proc = CliProcess(argv, child_env({"CLAUDE_CONFIG_DIR": str(home)}), cwd=home)
        await self._proc.start()

    async def events(self) -> AsyncIterator[Any]:
        assert self._proc is not None and self._home is not None
        asked = False
        async for line in self._proc.lines():
            found = AUTHORIZE.search(line)
            if found and not asked:
                asked = True
                yield Url(url=found.group(0))
                yield NeedsInput(hint=PASTE_HINT)
        status = await self._proc.wait()
        if status == 0 and (self._home / ".credentials.json").exists():
            yield Success()
            return
        text = self._proc.excerpt()
        kind = "code_refused" if "Login failed" in text else "login_failed"
        yield Failure(kind=kind, excerpt=text or f"claude auth login ended with exit code {status}")

    async def submit(self, text: str) -> None:
        assert self._proc is not None
        await self._proc.write(text)

    async def cancel(self) -> None:
        if self._proc is not None:
            self._proc.kill()

    async def identity(self, home: Path) -> Identity | None:
        status, out = await run_once(
            [*self._cli(), "auth", "status", "--json"], child_env({"CLAUDE_CONFIG_DIR": str(home)}), home, 60
        )
        try:
            data = json.loads(out[out.index("{") :])
        except ValueError:
            return None
        if status != 0 or not isinstance(data, dict) or not data.get("loggedIn"):
            return None
        email = str(data.get("email") or "")
        account = email.lower() or str(data.get("orgId") or "")
        if not account:
            return None
        plan = data.get("subscriptionType")
        org = data.get("orgName")
        return Identity(
            provider_account_id=account,
            email=email,
            plan=str(plan) if plan else None,
            org=str(org) if org else None,
        )

    async def models(self, home: Path) -> list[str]:
        return list(MODELS)

    async def test_login(self, home: Path) -> CheckResult:
        status, out = await run_once(
            [*self._cli(), "-p", TEST_PROMPT], child_env({"CLAUDE_CONFIG_DIR": str(home)}), home, 240
        )
        ok = status == 0 and "PONG" in out.upper()
        return CheckResult(ok=ok, detail=None if ok else redact(out.strip()[-400:]) or f"exit code {status}")

    def promote(self, staging: Path, home: Path) -> None:
        """A new account takes the whole staging home; an existing one keeps its settings and history and only
        gets the new credentials and the signed-in account (``oauthAccount``)."""
        if not home.exists() or not any(home.iterdir()):
            swap_home(staging, home)
            return
        replace_files(staging, home, (".credentials.json",))
        _merge_account(staging / ".claude.json", home / ".claude.json")


def _merge_account(source: Path, target: Path) -> None:
    try:
        fresh = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(fresh, dict) or "oauthAccount" not in fresh:
        return
    try:
        current = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (OSError, ValueError):
        current = {}
    if not isinstance(current, dict):
        current = {}
    current["oauthAccount"] = fresh["oauthAccount"]
    staged = target.with_name(f".{target.name}.farm-new")
    staged.write_text(json.dumps(current, indent=2), encoding="utf-8")
    os.replace(staged, target)
