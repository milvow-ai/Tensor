"""Claude CLI agent executor."""

import json
from typing import Any

from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.executors.cli_agent.base import (
    BaseCliAgentExecutor,
    clean_cli_text,
    parse_json_or_none,
    run_cli_process,
)
from farm.executors.cli_agent.classify import classify_claude


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
        verdict = classify_claude(out.stdout, out.stderr, out.exit_code)
        sess_id = data.get("session_id") if isinstance(data, dict) else None
        sess_id = sess_id or session_id

        if not verdict.ok:
            return self.build_exec_result(
                ok=False,
                ai="claude",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=data if isinstance(data, dict) else None,
                duration_s=out.duration_s,
                error_kind=verdict.kind,
                error=verdict.message or "Unknown Claude CLI error",
                reset_at=verdict.reset_at,
                session_id=str(sess_id) if sess_id else None,
            )

        if isinstance(data, dict):
            result_text = str(data.get("result", ""))
        else:
            result_text = clean_cli_text(out.stdout)

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
