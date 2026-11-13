"""Gemini CLI's native Google code flow and zero-prompt ACP model listing."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from farm.connect.drivers.base import Failure, Identity, LoginEvent, NeedsInput, Success, Url
from farm.connect.drivers.process import CliProcess, replace_files, resolve_cli
from farm.connect.homes import promote
from farm.executors.cli_agent.base import build_child_env
from farm.secrets import register_secret

_USER_INFO = "https://www.googleapis.com/oauth2/v2/userinfo"
_URL = re.compile(r"https://accounts\.google\.com/[^\s\x1b]+")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")
_SETTINGS = {
    "security": {"auth": {"selectedType": "oauth-personal"}},
    "memory": {"autoMemory": {"enabled": False}},
    "hooksConfig": {"enabled": False},
}


def _credentials(home: Path) -> Path:
    return home / ".gemini" / "oauth_creds.json"


def _env(home: Path) -> dict[str, str]:
    return build_child_env({
        "GEMINI_CLI_HOME": str(home),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "NO_BROWSER": "true",
        "GEMINI_FORCE_FILE_STORAGE": "true",
    })


def _write_settings(home: Path, settings: dict[str, Any]) -> None:
    target = home / ".gemini" / "settings.json"
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.write_text(json.dumps(settings), encoding="utf-8")


def _model_ids(result: Any) -> list[str]:
    models = result.get("models") if isinstance(result, dict) else None
    available = models.get("availableModels") if isinstance(models, dict) else None
    if not isinstance(available, list):
        return []
    ids = [model.get("modelId") for model in available[:250] if isinstance(model, dict)]
    return list(dict.fromkeys(model for model in ids if isinstance(model, str) and _MODEL.fullmatch(model)))


class GeminiPasteDriver:
    method = "paste_code"
    model_source = "gemini_cli_acp_model_list"

    def __init__(self, meta: Mapping[str, Any], *, command: Sequence[str] | None = None) -> None:
        if meta.get("cli_prefix"):
            raise NotImplementedError("This Gemini profile uses a separate runner. Sign in on that runner.")
        self._command = list(command) if command else None
        self._home: Path | None = None
        self._proc: CliProcess | None = None
        self._initialize: asyncio.Task[None] | None = None
        self._models: list[str] | None = None
        self._session_refused = False

    def _argv(self) -> list[str]:
        return [
            *(self._command or resolve_cli("gemini")), "--acp", "--skip-trust", "--extensions", "none",
            "--allowed-mcp-server-names", "farm-metadata-no-servers",
        ]

    def preflight(self) -> None:
        self._argv()

    async def start(self, home: Path, hint: str | None = None) -> None:
        self._home = home
        _write_settings(home, _SETTINGS)
        self._proc = CliProcess(self._argv(), _env(home), cwd=home)
        await self._proc.start()

    @staticmethod
    async def _rpc(proc: CliProcess, request_id: int, method: str, params: dict[str, Any]) -> None:
        await proc.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}))

    async def _catalog(self, proc: CliProcess, home: Path, line: str) -> bool:
        # Native readline may leave its prompt on the same line as the first ACP response.
        if "Enter the authorization code:" in line:
            _, marker, payload = line.partition("{")
            line = marker + payload
        try:
            reply = json.loads(line)
        except ValueError:
            return False
        if not isinstance(reply, dict):
            return False
        if reply.get("id") == 1:
            if not isinstance(reply.get("result"), dict):
                self._models = []
                return True
            await self._rpc(proc, 2, "session/new", {"cwd": str(home), "mcpServers": []})
        if reply.get("id") == 2:
            error = reply.get("error")
            self._session_refused = isinstance(error, dict) and error.get("code") != -32601
            self._models = _model_ids(reply.get("result"))
            return True
        return False

    async def _after_code(self, proc: CliProcess, home: Path) -> None:
        # The CLI consumes the code with readline, then caches the native credentials before starting ACP.
        try:
            async with asyncio.timeout(90):
                while not _credentials(home).is_file():
                    await asyncio.sleep(0.2)
            await self._rpc(proc, 1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        except (TimeoutError, OSError, RuntimeError):
            proc.kill()

    async def events(self) -> AsyncIterator[LoginEvent]:
        assert self._proc is not None and self._home is not None
        announced = False
        async for line in self._proc.lines():
            match = _URL.search(line)
            if match and not announced and urlsplit(match.group()).path.startswith("/o/oauth2/"):
                announced = True
                yield Url(match.group())
                yield NeedsInput("Sign in with the expected Google account and paste the code.")
                continue
            if self._initialize is not None and await self._catalog(self._proc, self._home, line):
                await self.cancel()
                if self._session_refused:
                    yield Failure(
                        "provider_access_refused",
                        "Google signed you in, but Gemini CLI refused the session for this account. "
                        "Consumer Google AI accounts use Antigravity CLI.",
                    )
                    return
                yield Success()
                return
        if self._initialize is not None and _credentials(self._home).is_file():
            # Older CLI versions may lack the metadata response. Identity is checked independently.
            self._models = []
            yield Success()
            return
        yield Failure("login_failed", "Gemini CLI sign-in failed. Check its version and try again.")

    async def submit(self, text: str) -> None:
        assert self._proc is not None and self._home is not None
        code = text.strip()
        if not code or len(code) > 1024 or any(char.isspace() for char in code):
            raise ValueError("Paste the authorization code shown by Google.")
        if self._initialize is not None:
            raise ValueError("This sign-in code was already submitted.")
        await self._proc.write(code)
        self._initialize = asyncio.create_task(self._after_code(self._proc, self._home))

    async def cancel(self) -> None:
        if self._initialize is not None:
            self._initialize.cancel()
            await asyncio.gather(self._initialize, return_exceptions=True)
        if self._proc is not None:
            self._proc.kill()
            await self._proc.wait()

    async def identity(self, home: Path) -> Identity | None:
        try:
            data = json.loads(_credentials(home).read_text(encoding="utf-8"))
            token = data.get("access_token") if isinstance(data, dict) else None
            if not isinstance(token, str) or not token:
                return None
            register_secret(token)
            async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
                response = await client.get(_USER_INFO, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                profile = response.json()
        except (OSError, ValueError, httpx.HTTPError):
            return None
        if not isinstance(profile, dict) or profile.get("verified_email") is not True:
            return None
        subject, email = profile.get("id"), profile.get("email")
        if not isinstance(subject, str) or not subject or not isinstance(email, str) or "@" not in email:
            return None
        name = profile.get("name")
        return Identity(subject, email, display_name=name if isinstance(name, str) else None)

    async def models(self, home: Path) -> list[str]:
        if self._models is not None:
            return self._models
        if not _credentials(home).is_file():
            return []
        # A probe does not read the user's project settings, hooks, or MCP configuration.
        with tempfile.TemporaryDirectory(prefix=".gemini-metadata-", dir=home.parent) as folder:
            scratch = Path(folder)
            _write_settings(scratch, _SETTINGS)
            target = _credentials(scratch)
            target.write_bytes(_credentials(home).read_bytes())
            target.chmod(0o600)
            proc = CliProcess(self._argv(), _env(scratch), cwd=scratch)
            try:
                await proc.start()
                await self._rpc(proc, 1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
                async with asyncio.timeout(30):
                    async for line in proc.lines():
                        if _URL.search(line):  # metadata refresh never asks the user to sign in
                            return []
                        if await self._catalog(proc, scratch, line):
                            return self._models or []
            except (TimeoutError, OSError, RuntimeError):
                return []
            finally:
                proc.kill()
                await proc.wait()
        return []

    def promote(self, staging: Path, home: Path) -> None:
        if not home.exists():
            promote(staging, home)
            return
        target = home / ".gemini" / "settings.json"
        settings: dict[str, Any] = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
        security = settings.setdefault("security", {})
        security.setdefault("auth", {})["selectedType"] = "oauth-personal"
        _write_settings(staging, settings)
        replace_files(
            staging / ".gemini",
            home / ".gemini",
            ("oauth_creds.json", "google_accounts.json", "settings.json"),
        )
