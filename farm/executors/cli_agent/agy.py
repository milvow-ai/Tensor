"""Google Antigravity (`agy`) CLI agent executor.

Note on multiple accounts:
As discovered from `agy --help` and Antigravity documentation, the Antigravity CLI
does not currently expose a configuration directory flag or environment variable
(its credentials and settings are stored globally in `~/.gemini/antigravity-cli`).
Therefore, only a single global Antigravity account is supported at this time.
"""

import json
import re
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

        argv: list[str] = [cli_bin, "-p", str(task), "--output-format", "json"]

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

        env: dict[str, str] = {}
        if "env" in meta and isinstance(meta["env"], dict):
            for k, v in meta["env"].items():
                env[str(k)] = str(v)

        out = await run_cli_process(
            argv,
            cwd=cwd,
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
