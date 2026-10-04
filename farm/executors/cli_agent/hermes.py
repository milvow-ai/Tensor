"""Hermes CLI agent executor."""

import json
import re
import tempfile
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


class HermesCliExecutor(BaseCliAgentExecutor):
    """Executes tasks via the Hermes CLI (`hermes -z` one-shot mode)."""

    async def execute(self, req: ExecRequest) -> ExecResult:
        meta = req.connection.meta or {}
        cli_bin = meta.get("cli_path", "hermes")
        profile = meta.get("profile", "farm-agent")

        task = req.params.get("task") or req.params.get("prompt", "")
        model = req.params.get("model") or meta.get("model") or meta.get("default_model")
        mode = req.params.get("mode", "answer")
        session_id = req.params.get("session_id")
        cwd = req.params.get("cwd")
        timeout_s = float(req.params.get("timeout_s", req.timeout_s))
        json_schema = req.params.get("json_schema")

        # Create temporary file for usage output
        temp_usage = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        temp_usage.close()
        usage_path = Path(temp_usage.name)

        argv: list[str] = [
            cli_bin,
            "-p",
            str(profile),
            "-z",
            str(task),
            "--usage-file",
            str(usage_path),
        ]

        if model:
            argv.extend(["-m", str(model)])
        if session_id:
            argv.extend(["--resume", str(session_id)])
        if cwd:
            argv.extend(["--in", str(cwd)])
        if mode == "answer":
            # For answer mode, can specify empty toolsets to restrict actions
            argv.extend(["--toolsets", ""])

        env: dict[str, str] = {}
        # Pin TERMINAL_CWD to prevent subprocess escaping cwd
        pinned_cwd = str(cwd) if cwd else str(Path.cwd())
        env["TERMINAL_CWD"] = pinned_cwd

        if "env" in meta and isinstance(meta["env"], dict):
            for k, v in meta["env"].items():
                env[str(k)] = str(v)

        try:
            out = await run_cli_process(
                argv,
                cwd=cwd,
                env=env,
                timeout_s=timeout_s,
            )
        finally:
            usage_data: dict[str, Any] = {}
            if usage_path.exists():
                try:
                    content = usage_path.read_text(encoding="utf-8")
                    parsed = json.loads(content)
                    if isinstance(parsed, dict):
                        usage_data = parsed
                except Exception:
                    pass
                try:
                    usage_path.unlink()
                except Exception:
                    pass

        if out.timed_out:
            return self.build_exec_result(
                ok=False,
                ai="hermes",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=usage_data,
                duration_s=out.duration_s,
                error_kind=ErrorKind.TIMEOUT,
                error=out.stderr or f"Hermes CLI timed out after {timeout_s}s",
            )

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
                ai="hermes",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=usage_data,
                duration_s=out.duration_s,
                error_kind=ErrorKind.NEEDS_LOGIN,
                error=clean_cli_text(
                    out.stderr or out.stdout or "Hermes authentication required; please log in"
                ),
            )

        # Check for usage limits / credit balance errors
        limit_patterns = [
            r"usage limit",
            r"rate limit",
            r"quota exceeded",
            r"exceeded your current quota",
            r"insufficient balance",
            r"insufficient funds",
            r"overloaded",
            r"resets?\s+at",
            r"resets?\s+in",
            r"\b429\b",
        ]
        if any(re.search(pat, combined_text, re.IGNORECASE) for pat in limit_patterns):
            reset_at = parse_reset_at(combined_text, usage_data)
            return self.build_exec_result(
                ok=False,
                ai="hermes",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=usage_data,
                duration_s=out.duration_s,
                error_kind=ErrorKind.LIMIT_REACHED,
                error=clean_cli_text(out.stderr or out.stdout or "Hermes quota / rate limit reached"),
                reset_at=reset_at,
            )

        failed = bool(usage_data.get("failed", False))
        interrupted = bool(usage_data.get("interrupted", False))
        is_error = failed or interrupted or out.exit_code != 0

        sess_id = usage_data.get("session_id") or session_id
        text_response = clean_cli_text(out.stdout)

        if is_error and not text_response:
            return self.build_exec_result(
                ok=False,
                ai="hermes",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=usage_data,
                duration_s=out.duration_s,
                error_kind=ErrorKind.SERVER,
                error=clean_cli_text(out.stderr or out.stdout or "Hermes execution failed"),
                session_id=str(sess_id) if sess_id else None,
            )

        parsed_json: Any = None
        if json_schema or (text_response.startswith("{") or text_response.startswith("[")):
            parsed_json = parse_json_or_none(text_response)

        return self.build_exec_result(
            ok=True,
            text=text_response,
            session_id=str(sess_id) if sess_id else None,
            ai="hermes",
            model=str(model) if model else None,
            connection=req.connection,
            raw_data=usage_data,
            parsed_json=parsed_json,
            duration_s=out.duration_s,
        )
