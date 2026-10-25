"""Codex (ChatGPT) accounts sign in with a device code: the person opens a link and types a short code.

``codex login --device-auth`` prints the link and the code, then waits until the person approves (15 minutes
at most) and writes ``auth.json`` into ``CODEX_HOME``. Codex refreshes that login itself afterwards.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from farm.connect.drivers.base import Failure, Identity, Success, UserCode
from farm.connect.drivers.process import CliProcess, child_env, replace_files, resolve_cli, run_once
from farm.connect.homes import promote as swap_home
from farm.connect.service import CheckResult
from farm.secrets import redact

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")  # codex 0.160 colours the link and the code even on a pipe
URL = re.compile(r"https://\S+")
CODE = re.compile(r"^\s*([A-Z0-9]{4}-[A-Z0-9]{4,6})\s*$")
FILE_STORE = 'cli_auth_credentials_store = "file"'  # the login lives in CODEX_HOME, never in a keyring
TEST_PROMPT = "Respond with only the word PONG"
WSL_LATER = "This Codex account runs inside WSL; signing it in from the dashboard comes with the server move."


def ensure_file_store(config: Path) -> None:
    text = config.read_text(encoding="utf-8") if config.exists() else ""
    if "cli_auth_credentials_store" not in text:
        config.write_text(f"{FILE_STORE}\n{text}", encoding="utf-8")


def _claims(token: Any) -> dict[str, Any]:
    if not isinstance(token, str) or token.count(".") != 2:
        return {}
    part = token.split(".")[1]
    try:
        decoded = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def codex_identity(auth_file: Path) -> Identity | None:
    """Who a Codex login belongs to, from the id token's claims (read locally, nothing is sent anywhere)."""
    try:
        data = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict):
        return None
    claims = _claims(tokens.get("id_token"))
    openai = claims.get("https://api.openai.com/auth")
    openai = openai if isinstance(openai, dict) else {}
    account = openai.get("chatgpt_user_id") or claims.get("sub") or tokens.get("account_id")
    if not account:
        return None
    plan = openai.get("chatgpt_plan_type")
    return Identity(
        provider_account_id=str(account),
        email=str(claims.get("email") or ""),
        plan=str(plan) if plan else None,
        org=None,
    )


class CodexDeviceDriver:
    method = "device_code"

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
        return self._command or resolve_cli("codex")

    async def start(self, home: Path, hint: str | None = None) -> None:
        home.mkdir(parents=True, exist_ok=True)
        ensure_file_store(home / "config.toml")
        self._home = home
        self._proc = CliProcess(
            [*self._cli(), "login", "--device-auth"],
            child_env({"CODEX_HOME": str(home), "NO_COLOR": "1"}),
            cwd=home,
        )
        await self._proc.start()

    async def events(self) -> AsyncIterator[Any]:
        assert self._proc is not None and self._home is not None
        link: str | None = None
        async for raw in self._proc.lines():
            line = ANSI.sub("", raw)
            found = URL.search(line)
            if link is None and found:
                link = found.group(0).rstrip(".,)")
                continue
            code = CODE.match(line)
            if code and link:
                yield UserCode(code=code.group(1), verification_uri=link)
        status = await self._proc.wait()
        if status == 0 and (self._home / "auth.json").exists():
            yield Success()
            return
        text = self._proc.excerpt()
        kind = "expired" if "timed out" in text else "login_failed"
        yield Failure(kind=kind, excerpt=text or f"codex login ended with exit code {status}")

    async def submit(self, text: str) -> None:
        raise RuntimeError("a device-code sign-in takes no pasted code")

    async def cancel(self) -> None:
        if self._proc is not None:
            self._proc.kill()

    async def identity(self, home: Path) -> Identity | None:
        return codex_identity(home / "auth.json")

    async def models(self, home: Path) -> list[str]:
        return []  # Codex lists no models; the pool keeps the ones it has

    async def test_login(self, home: Path) -> CheckResult:
        argv = [*self._cli(), "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", TEST_PROMPT]
        status, out = await run_once(argv, child_env({"CODEX_HOME": str(home)}), home, 240)
        ok = status == 0 and "PONG" in out.upper()
        return CheckResult(ok=ok, detail=None if ok else redact(out.strip()[-400:]) or f"exit code {status}")

    def promote(self, staging: Path, home: Path) -> None:
        """A new account takes the whole staging home; an existing one only gets the new ``auth.json``."""
        if not home.exists() or not any(home.iterdir()):
            swap_home(staging, home)
            return
        replace_files(staging, home, ("auth.json",))
        ensure_file_store(home / "config.toml")
