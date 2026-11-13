"""Google Gemini CLI (`gemini`, npm @google/gemini-cli) agent executor.

One Google login per connection: `meta.home` redirects `USERPROFILE`/`HOME`, so the CLI keeps its
`~/.gemini` settings and OAuth credentials under that directory (same mechanism as the agy driver).
Output parsing is shared with the agy driver.
"""

import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from farm.ai.worker_bootstrap import SERVER_NAME, TOKEN_ENV, current_worker_launch
from farm.executors.base import ConnectionView
from farm.executors.cli_agent.agy import AgyCliExecutor, _launch_env
from farm.executors.cli_agent.base import SubprocessOutput, run_cli_process
from farm.executors.cli_agent.classify import classify_gemini

_MAX_SETTINGS_BYTES = 65536


def _trusted_worker_settings(path: Path, url: str) -> bool:
    """Gemini CLI rejects user-owned system settings and untrusted parents. Match that on POSIX."""
    if os.name != "posix" or not path.is_absolute() or ".." in path.parts:
        return False
    try:
        for location in [path, *path.parents]:
            info = location.lstat()
            expected_type = stat.S_ISREG if location == path else stat.S_ISDIR
            if info.st_uid != 0 or info.st_mode & 0o022 or not expected_type(info.st_mode):
                return False
        with path.open("rb") as stream:
            settings = json.loads(stream.read(_MAX_SETTINGS_BYTES + 1))
        server = settings["mcpServers"][SERVER_NAME]
        redaction = settings["security"]["environmentVariableRedaction"]
        return (
            server == {
                "httpUrl": url,
                "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"},
                "trust": True,
            }
            and redaction["enabled"] is True and TOKEN_ENV in redaction["blocked"]
            and isinstance(redaction["allowed"], list)
            and TOKEN_ENV not in {item.upper() for item in redaction["allowed"]}
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


class GeminiCliExecutor(AgyCliExecutor):
    """Executes tasks via the official Gemini CLI in headless mode."""

    default_bin = "gemini"
    classify = staticmethod(classify_gemini)

    async def list_models(self, connection: ConnectionView) -> list[str] | None:
        # `gemini models` is a positional user prompt, unlike `agy models`. Never infer during discovery.
        return None

    def build_argv(
        self,
        cli_bin: str,
        task: str,
        model: Any,
        session_id: Any,
        mode: str,
        json_schema: Any,
    ) -> list[str]:
        argv: list[str] = [cli_bin, "-p", task, "--output-format", "json", "--skip-trust"]
        if model:
            argv.extend(["--model", str(model)])
        if session_id:
            argv.extend(["--resume", str(session_id)])
        if mode == "edit":
            argv.extend(["--approval-mode", "yolo"])
        return argv

    async def run_process(
        self, argv: list[str], *, cwd: str | None, meta: Mapping[str, Any], timeout_s: float,
    ) -> SubprocessOutput:
        env = _launch_env(meta)
        if home := meta.get("home"):
            env["GEMINI_CLI_HOME"] = str(home)
        worker = None if meta.get("cli_prefix") else current_worker_launch.get()
        settings = os.environ.get("FARM_WORKER_GEMINI_SETTINGS")
        if worker is None or not settings or not _trusted_worker_settings(Path(settings), worker.url):
            return await run_cli_process(argv, cwd=cwd, env=env, timeout_s=timeout_s)
        env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = settings
        argv.extend(["--allowed-mcp-server-names", SERVER_NAME, "--extensions", "none"])
        return await run_cli_process(argv, cwd=cwd, env=env, timeout_s=timeout_s, worker=worker)
