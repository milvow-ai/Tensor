"""Google Antigravity (`agy`) CLI agent executor.

Multiple accounts: `agy --help` shows no configuration-directory flag, and the binary reads no variable
for one; it keeps its state (and, as far as is known, its login) under `~/.gemini/antigravity-cli`,
resolved from the user's home directory. A connection with `meta.home` therefore runs `agy` with
`USERPROFILE` and `HOME` pointing at that directory, so each connection owns a separate state and login
(`farm ai login <connection>` starts `agy` the same way). Without `meta.home` the global login is used,
as before. Not verified live: whether a login made under a redirected home stays there depends on where
`agy` stores its token, so verify the first extra account with `farm ai test <connection>`.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.executors.cli_agent.base import (
    BaseCliAgentExecutor,
    clean_cli_text,
    parse_json_or_none,
    parse_reset_at,
    run_cli_process,
)


class AgyCliExecutor(BaseCliAgentExecutor):
    """Executes tasks via the Google Antigravity CLI (`agy`)."""

    async def execute(self, req: ExecRequest) -> ExecResult:
        meta = req.connection.meta or {}
        cli_bin = meta.get("cli_path", "agy")

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

        task_str = str(task)
        argv: list[str] = [cli_bin, "-p", task_str, "--output-format", "json"]

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

        env: dict[str, str] = {}
        home = meta.get("home")
        if home:
            if not Path(str(home)).is_absolute():
                return self.build_exec_result(
                    ok=False,
                    ai="gemini",
                    model=str(model) if model else None,
                    connection=req.connection,
                    error_kind=ErrorKind.BAD_REQUEST,
                    error=f"meta.home must be an absolute path, got {home!r}",
                )
            env["USERPROFILE"] = str(home)
            env["HOME"] = str(home)
        if "env" in meta and isinstance(meta["env"], dict):
            for k, v in meta["env"].items():
                env[str(k)] = str(v)

        out = await run_cli_process(
            argv,
            cwd=resolved_cwd or cwd,
            env=env,
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
        combined_text = f"{out.stdout} {out.stderr}".strip()

        # Check for auth errors
        auth_patterns = [
            r"not logged in",
            r"please log in",
            r"authentication required",
            r"unauthorized",
            r"invalid api key",
            r"token expired",
            r"\b401\b",
            r"\b403\b",
        ]
        if any(re.search(pat, combined_text, re.IGNORECASE) for pat in auth_patterns):
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=data if isinstance(data, dict) else None,
                duration_s=out.duration_s,
                error_kind=ErrorKind.NEEDS_LOGIN,
                error=clean_cli_text(
                    out.stderr or out.stdout or "Antigravity authentication required; please log in"
                ),
            )

        # Check for usage limits / quota exhaustion
        limit_patterns = [
            r"usage limit",
            r"rate limit",
            r"quota exceeded",
            r"resource_exhausted",
            r"exceeded your current quota",
            r"overloaded",
            r"resets?\s+at",
            r"resets?\s+in",
            r"\b429\b",
        ]
        if any(re.search(pat, combined_text, re.IGNORECASE) for pat in limit_patterns):
            reset_at = parse_reset_at(combined_text, data if isinstance(data, dict) else None)
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=data if isinstance(data, dict) else None,
                duration_s=out.duration_s,
                error_kind=ErrorKind.LIMIT_REACHED,
                error=clean_cli_text(out.stderr or out.stdout or "Antigravity quota / rate limit reached"),
                reset_at=reset_at,
            )

        status = ""
        response_text = ""
        conv_id = session_id
        is_error = False
        error_msg: str | None = None

        if isinstance(data, dict):
            status = str(data.get("status", ""))
            response_text = str(data.get("response", data.get("result", "")))
            conv_id = data.get("conversation_id") or data.get("session_id") or conv_id
            if status and status != "SUCCESS":
                is_error = True
                error_msg = str(data.get("error") or data.get("message") or f"agy status: {status}")
        else:
            if out.exit_code != 0:
                is_error = True
                error_msg = clean_cli_text(
                    out.stderr or out.stdout or f"agy exited with code {out.exit_code}"
                )
            else:
                response_text = clean_cli_text(out.stdout)

        if is_error or (out.exit_code != 0 and not response_text):
            return self.build_exec_result(
                ok=False,
                ai="gemini",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=data if isinstance(data, dict) else None,
                duration_s=out.duration_s,
                error_kind=ErrorKind.SERVER if out.exit_code != 0 else ErrorKind.BAD_REQUEST,
                error=error_msg or "Unknown agy CLI error",
                session_id=str(conv_id) if conv_id else None,
            )

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
