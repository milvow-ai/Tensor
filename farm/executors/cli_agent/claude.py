"""Claude CLI agent executor."""

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


class ClaudeCliExecutor(BaseCliAgentExecutor):
    """Executes tasks via the Claude Code CLI (`claude`)."""

    async def execute(self, req: ExecRequest) -> ExecResult:
        meta = req.connection.meta or {}
        cli_bin = meta.get("cli_path", "claude")
        config_dir = meta.get("config_dir")

        task = req.params.get("task") or req.params.get("prompt", "")
        model = req.params.get("model") or meta.get("model") or meta.get("default_model")
        effort = req.params.get("effort") or meta.get("effort") or meta.get("default_effort")
        mode = req.params.get("mode", "answer")
        session_id = req.params.get("session_id")
        cwd = req.params.get("cwd")
        timeout_s = float(req.params.get("timeout_s", req.timeout_s))
        json_schema = req.params.get("json_schema")

        ok, err_msg = self.validate_cli_identifiers(session_id=session_id, model=model, effort=effort)
        if not ok:
            return self.build_exec_result(
                ok=False,
                ai="claude",
                model=str(model) if model else None,
                effort=str(effort) if effort else None,
                connection=req.connection,
                error_kind=ErrorKind.BAD_REQUEST,
                error=err_msg or "Invalid session_id, model, or effort",
            )

        ok, err_msg, resolved_cwd = self.validate_confinement(req, mode, cwd)
        if not ok:
            return self.build_exec_result(
                ok=False,
                ai="claude",
                model=str(model) if model else None,
                effort=str(effort) if effort else None,
                connection=req.connection,
                error_kind=ErrorKind.BAD_REQUEST,
                error=err_msg or "Confinement violation",
            )

        # Claude Code reads prompt from stdin when omitted after -p
        argv: list[str] = [cli_bin, "-p", "--output-format", "json"]

        if model:
            argv.extend(["--model", str(model)])
        if effort:
            argv.extend(["--effort", str(effort)])
        if session_id:
            argv.extend(["--resume", str(session_id)])
        if mode == "edit":
            argv.extend(["--permission-mode", "acceptEdits"])
        else:
            argv.extend(["--allowedTools", ""])
        if json_schema:
            schema_str = json.dumps(json_schema) if isinstance(json_schema, dict) else str(json_schema)
            argv.extend(["--json-schema", schema_str])

        env: dict[str, str] = {}
        if config_dir:
            env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        if "env" in meta and isinstance(meta["env"], dict):
            for k, v in meta["env"].items():
                env[str(k)] = str(v)

        out = await run_cli_process(
            argv,
            input_text=str(task),
            cwd=resolved_cwd or cwd,
            env=env,
            timeout_s=timeout_s,
        )

        if out.timed_out:
            return self.build_exec_result(
                ok=False,
                ai="claude",
                model=str(model) if model else None,
                connection=req.connection,
                duration_s=out.duration_s,
                error_kind=ErrorKind.TIMEOUT,
                error=out.stderr or f"Claude CLI timed out after {timeout_s}s",
            )

        data = parse_json_or_none(out.stdout)
        combined_text = f"{out.stdout} {out.stderr}".strip()

        # Check for auth errors first
        auth_patterns = [
            r"not logged in",
            r"please log in",
            r"claude login",
            r"/login",
            r"authentication required",
            r"unauthorized",
            r"invalid api key",
            r"token expired",
            r"setup-token",
            r"\b401\b",
            r"\b403\b",
        ]
        is_auth = any(re.search(pat, combined_text, re.IGNORECASE) for pat in auth_patterns)
        if is_auth:
            return self.build_exec_result(
                ok=False,
                ai="claude",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=data if isinstance(data, dict) else None,
                duration_s=out.duration_s,
                error_kind=ErrorKind.NEEDS_LOGIN,
                error=clean_cli_text(out.stderr or out.stdout or "Authentication required; please log in"),
            )

        # Check for usage limits
        limit_patterns = [
            r"usage limit",
            r"rate limit",
            r"exceeded your current quota",
            r"quota exceeded",
            r"overloaded",
            r"resets?\s+at",
            r"resets?\s+in",
            r"capacity",
            r"\b429\b",
        ]
        is_limit = any(re.search(pat, combined_text, re.IGNORECASE) for pat in limit_patterns)
        if is_limit:
            reset_at = parse_reset_at(combined_text, data if isinstance(data, dict) else None)
            return self.build_exec_result(
                ok=False,
                ai="claude",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=data if isinstance(data, dict) else None,
                duration_s=out.duration_s,
                error_kind=ErrorKind.LIMIT_REACHED,
                error=clean_cli_text(out.stderr or out.stdout or "Claude usage limit reached"),
                reset_at=reset_at,
            )

        # If process exited with non-zero or JSON says is_error
        is_error = False
        error_msg: str | None = None
        result_text = ""
        sess_id = session_id

        if isinstance(data, dict):
            is_error = bool(data.get("is_error", False))
            if is_error:
                error_msg = str(data.get("result") or data.get("error") or "Claude execution error")
            else:
                result_text = str(data.get("result", ""))
            sess_id = data.get("session_id") or sess_id
        else:
            if out.exit_code != 0:
                is_error = True
                error_msg = clean_cli_text(
                    out.stderr or out.stdout or f"Process exited with code {out.exit_code}"
                )
            else:
                result_text = clean_cli_text(out.stdout)

        if is_error or out.exit_code != 0:
            return self.build_exec_result(
                ok=False,
                ai="claude",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=data if isinstance(data, dict) else None,
                duration_s=out.duration_s,
                error_kind=ErrorKind.SERVER if out.exit_code != 0 else ErrorKind.BAD_REQUEST,
                error=error_msg or "Unknown Claude CLI error",
                session_id=str(sess_id) if sess_id else None,
            )

        # Parse JSON if response is JSON or schema requested
        parsed_json: Any = None
        if json_schema or (result_text.startswith("{") or result_text.startswith("[")):
            parsed_json = parse_json_or_none(result_text)

        return self.build_exec_result(
            ok=True,
            text=result_text,
            session_id=str(sess_id) if sess_id else None,
            ai="claude",
            model=str(model) if model else None,
            effort=str(effort) if effort else None,
            effort_applied=True if effort else None,
            connection=req.connection,
            raw_data=data if isinstance(data, dict) else None,
            parsed_json=parsed_json,
            duration_s=out.duration_s,
        )
