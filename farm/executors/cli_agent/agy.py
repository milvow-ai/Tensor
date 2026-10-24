"""Google Antigravity (`agy`) CLI agent executor.

Multiple accounts: `agy --help` shows no configuration-directory flag, and the binary reads no variable
for one; it keeps its state (and, as far as is known, its login) under `~/.gemini/antigravity-cli`,
resolved from the user's home directory. A connection with `meta.home` therefore runs `agy` with
`USERPROFILE` and `HOME` pointing at that directory, so each connection owns a separate state and login
(`farm ai login <connection>` starts `agy` the same way). Without `meta.home` the global login is used,
as before. Not verified live: whether a login made under a redirected home stays there depends on where
`agy` stores its token, so verify the first extra account with `farm ai test <connection>`.

Models: `agy models` lists the model ids this agy accepts, one `<id>` TAB `<display name>` line each.
`list_models` runs it the same way as a task, so `farm ai test` can check an account's configured models
against it. An agy update can drop a model (gemini-3.1-pro-high, 2026-10).
"""

import json
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.executors.cli_agent.base import (
    BaseCliAgentExecutor,
    clean_cli_text,
    parse_json_or_none,
    run_cli_process,
)
from farm.executors.cli_agent.classify import Classification, classify_agy

MODELS_TIMEOUT_S = 90.0


def parse_models_list(stdout: str) -> list[str]:
    """Model ids from `agy models` output (`<id>` TAB `<display name>` per line; other lines are skipped)."""
    ids: list[str] = []
    for line in clean_cli_text(stdout).splitlines():
        model_id, tab, _name = line.partition("\t")
        model_id = model_id.strip()
        if tab and BaseCliAgentExecutor._SAFE_MODEL_PATTERN.match(model_id):
            ids.append(model_id)
    return ids


def _with_prefix(meta: Mapping[str, Any], argv: list[str]) -> list[str]:
    # meta.cli_prefix runs the CLI through a launcher, e.g. WSL as a separate Linux user so each account
    # has its own file-based login:
    # [wsl.exe, -d, Ubuntu-24.04, -u, agy2, -e, env, -u, DBUS_SESSION_BUS_ADDRESS]
    prefix = meta.get("cli_prefix")
    if isinstance(prefix, list) and prefix:
        return [str(p) for p in prefix] + argv
    return argv


def _launch_env(meta: Mapping[str, Any]) -> dict[str, str]:
    """``USERPROFILE`` / ``HOME`` from ``meta.home`` (the caller checks it is absolute) and ``meta.env``."""
    env: dict[str, str] = {}
    home = meta.get("home")
    if home:
        env["USERPROFILE"] = str(home)
        env["HOME"] = str(home)
    if "env" in meta and isinstance(meta["env"], dict):
        for k, v in meta["env"].items():
            env[str(k)] = str(v)
    return env


class AgyCliExecutor(BaseCliAgentExecutor):
    """Executes tasks via the Google Antigravity CLI (`agy`)."""

    default_bin = "agy"
    classify: Callable[[str, str, int], Classification] = staticmethod(classify_agy)

    async def list_models(self, connection: ConnectionView) -> list[str] | None:
        """The model ids this account's agy accepts, or ``None`` when agy could not list them."""
        meta = connection.meta or {}
        home = meta.get("home")
        if home and not Path(str(home)).is_absolute():
            return None
        argv = _with_prefix(meta, [str(meta.get("cli_path", self.default_bin)), "models"])
        out = await run_cli_process(argv, cwd=None, env=_launch_env(meta), timeout_s=MODELS_TIMEOUT_S)
        if out.timed_out or out.exit_code != 0:
            return None
        return parse_models_list(out.stdout) or None

    def build_argv(
        self,
        cli_bin: str,
        task: str,
        model: Any,
        session_id: Any,
        mode: str,
        json_schema: Any,
    ) -> list[str]:
        argv: list[str] = [cli_bin, "-p", task, "--output-format", "json"]
        if model:
            argv.extend(["--model", str(model)])
        if session_id:
            argv.extend(["--conversation", str(session_id)])
        if mode == "edit":
            argv.extend(["--mode", "accept-edits", "--dangerously-skip-permissions"])
        else:
            argv.extend(["--mode", "plan"])
        if json_schema:
            schema_str = json.dumps(json_schema) if isinstance(json_schema, dict) else str(json_schema)
            argv.extend(["--json-schema", schema_str])
        return argv

    async def execute(self, req: ExecRequest) -> ExecResult:
        meta = req.connection.meta or {}
        cli_bin = meta.get("cli_path", self.default_bin)

        task = req.params.get("task") or req.params.get("prompt", "")
        model = req.params.get("model") or meta.get("model") or meta.get("default_model")
        mode = req.params.get("mode", "answer")
        session_id = req.params.get("session_id")
        cwd = req.params.get("cwd")
        timeout_s = float(req.params.get("timeout_s", req.timeout_s))
        json_schema = req.params.get("json_schema")

        ok, err_msg = self.validate_cli_identifiers(session_id=session_id, model=model)
        if not ok:
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                error_kind=ErrorKind.BAD_REQUEST,
                error=err_msg or "Invalid session_id or model",
            )

        ok, err_msg, resolved_cwd = self.validate_confinement(req, mode, cwd)
        if not ok:
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                error_kind=ErrorKind.BAD_REQUEST,
                error=err_msg or "Confinement violation",
            )

        argv = self.build_argv(str(cli_bin), str(task), model, session_id, mode, json_schema)
        argv = _with_prefix(meta, argv)

        cmdline_len = len(subprocess.list2cmdline(argv))
        if cmdline_len > 32000:
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                error_kind=ErrorKind.BAD_REQUEST,
                error=(
                    f"Command line length ({cmdline_len} chars) exceeds command-line length limit "
                    "(32000 chars) and agy does not support plain text stdin"
                ),
            )

        home = meta.get("home")
        if home and not Path(str(home)).is_absolute():
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                error_kind=ErrorKind.BAD_REQUEST,
                error=f"meta.home must be an absolute path, got {home!r}",
            )

        out = await run_cli_process(
            argv,
            cwd=resolved_cwd or cwd,
            env=_launch_env(meta),
            timeout_s=timeout_s,
        )

        if out.timed_out:
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                duration_s=out.duration_s,
                error_kind=ErrorKind.TIMEOUT,
                error=out.stderr or f"agy CLI timed out after {timeout_s}s",
            )

        data = parse_json_or_none(out.stdout)
        verdict = self.classify(out.stdout, out.stderr, out.exit_code)
        conv_id = session_id
        if isinstance(data, dict):
            conv_id = data.get("conversation_id") or data.get("session_id") or conv_id

        if not verdict.ok:
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=data if isinstance(data, dict) else None,
                duration_s=out.duration_s,
                error_kind=verdict.kind,
                error=verdict.message or "Unknown agy CLI error",
                reset_at=verdict.reset_at,
                session_id=str(conv_id) if conv_id else None,
            )

        if isinstance(data, dict):
            response_text = str(data.get("response", data.get("result", "")))
        else:
            response_text = clean_cli_text(out.stdout)

        parsed_json: Any = None
        if json_schema or (response_text.startswith("{") or response_text.startswith("[")):
            parsed_json = parse_json_or_none(response_text)

        return self.build_exec_result(
            ok=True,
            text=response_text,
            session_id=str(conv_id) if conv_id else None,
            ai="gemini",
            model=str(model) if model else None,
            connection=req.connection,
            raw_data=data if isinstance(data, dict) else None,
            parsed_json=parsed_json,
            duration_s=out.duration_s,
        )
