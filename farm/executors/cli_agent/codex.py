"""OpenAI Codex CLI agent executor."""

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
    parse_jsonl,
    parse_reset_at,
    run_cli_process,
)


class CodexCliExecutor(BaseCliAgentExecutor):
    """Executes tasks via the OpenAI Codex CLI (`codex exec`)."""

    async def execute(self, req: ExecRequest) -> ExecResult:
        meta = req.connection.meta or {}
        cli_bin = meta.get("cli_path", "codex")
        config_dir = meta.get("config_dir")

        task = str(req.params.get("task") or req.params.get("prompt", ""))
        model = req.params.get("model") or meta.get("model") or meta.get("default_model")
        mode = req.params.get("mode", "answer")
        session_id = req.params.get("session_id")
        cwd = req.params.get("cwd")
        timeout_s = float(req.params.get("timeout_s", req.timeout_s))
        json_schema = req.params.get("json_schema")

        # Handle optional json_schema via a temporary schema file
        temp_schema_file: Path | None = None
        schema_path_str: str | None = None
        if json_schema:
            try:
                tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
                json.dump(json_schema, tf)
                tf.close()
                temp_schema_file = Path(tf.name)
                schema_path_str = str(temp_schema_file)
            except Exception:
                pass

        argv: list[str] = [cli_bin, "exec"]
        if session_id:
            argv.extend(["resume", str(session_id)])

        argv.extend(["--json", "--skip-git-repo-check"])

        if model:
            argv.extend(["-m", str(model)])

        if mode == "edit":
            argv.extend(["--sandbox", "workspace-write"])
        else:
            argv.extend(["--sandbox", "read-only"])

        if cwd:
            argv.extend(["-C", str(cwd)])

        if schema_path_str:
            argv.extend(["--output-schema", schema_path_str])

        argv.append(task)

        env: dict[str, str] = {}
        if config_dir:
            env["CODEX_HOME"] = str(config_dir)
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
            if temp_schema_file and temp_schema_file.exists():
                try:
                    temp_schema_file.unlink()
                except Exception:
                    pass

        if out.timed_out:
            return self.build_exec_result(
                ok=False,
                ai="codex",
                model=str(model) if model else None,
                connection=req.connection,
                duration_s=out.duration_s,
                error_kind=ErrorKind.TIMEOUT,
                error=out.stderr or f"Codex CLI timed out after {timeout_s}s",
            )

        combined_text = f"{out.stdout} {out.stderr}".strip()

        # Parse JSON / JSONL
        single_json = parse_json_or_none(out.stdout)
        jsonl_events = parse_jsonl(out.stdout)

        # Check for auth errors
        auth_patterns = [
            r"not logged in",
            r"please log in",
            r"codex login",
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
                ai="codex",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=single_json if isinstance(single_json, dict) else None,
                duration_s=out.duration_s,
                error_kind=ErrorKind.NEEDS_LOGIN,
                error=clean_cli_text(
                    out.stderr or out.stdout or "Codex authentication required; please log in"
                ),
            )

        # Check for usage limits
        limit_patterns = [
            r"usage limit",
            r"rate limit",
            r"quota exceeded",
            r"exceeded your current quota",
            r"insufficient_quota",
            r"overloaded",
            r"resets?\s+at",
            r"resets?\s+in",
            r"\b429\b",
        ]
        if any(re.search(pat, combined_text, re.IGNORECASE) for pat in limit_patterns):
            reset_at = parse_reset_at(combined_text, single_json if isinstance(single_json, dict) else None)
            return self.build_exec_result(
                ok=False,
                ai="codex",
                model=str(model) if model else None,
                connection=req.connection,
                raw_data=single_json if isinstance(single_json, dict) else None,
                duration_s=out.duration_s,
                error_kind=ErrorKind.LIMIT_REACHED,
                error=clean_cli_text(out.stderr or out.stdout or "Codex usage limit reached"),
                reset_at=reset_at,
            )

        # Extract session_id, text, and usage from single JSON or JSONL
        extracted_text = ""
        extracted_session_id = session_id
        extracted_usage: dict[str, Any] = {}
        cost_usd = 0.0

        if isinstance(single_json, dict) and any(k in single_json for k in ("result", "response", "text")):
            extracted_text = str(
                single_json.get("result") or single_json.get("response") or single_json.get("text") or ""
            )
            extracted_session_id = (
                single_json.get("session_id") or single_json.get("thread_id") or extracted_session_id
            )
            if isinstance(single_json.get("usage"), dict):
                extracted_usage = single_json["usage"]
            cost_usd = float(single_json.get("total_cost_usd") or single_json.get("cost_usd") or 0.0)
        else:
            # Parse events stream
            text_parts: list[str] = []
            for ev in jsonl_events:
                ev_type = ev.get("type", "")
                if "session_id" in ev:
                    extracted_session_id = ev["session_id"]
                elif "thread_id" in ev:
                    extracted_session_id = ev["thread_id"]

                if ev_type in ("message", "assistant_message", "item.completed"):
                    # Check for text in various codex event schemas
                    if "text" in ev:
                        text_parts.append(str(ev["text"]))
                    elif "content" in ev:
                        content = ev["content"]
                        if isinstance(content, str):
                            text_parts.append(content)
                        elif isinstance(content, list):
                            for item in content:
                                if isinstance(item, dict) and item.get("text"):
                                    text_parts.append(str(item["text"]))
                    elif "item" in ev and isinstance(ev["item"], dict):
                        item = ev["item"]
                        if item.get("text"):
                            text_parts.append(str(item["text"]))

                if "usage" in ev and isinstance(ev["usage"], dict):
                    extracted_usage.update(ev["usage"])
                if "cost_usd" in ev or "total_cost_usd" in ev:
                    cost_usd = float(ev.get("cost_usd") or ev.get("total_cost_usd") or 0.0)

            extracted_text = "\n".join(text_parts).strip() if text_parts else clean_cli_text(out.stdout)

        if out.exit_code != 0 and not extracted_text:
            return self.build_exec_result(
                ok=False,
                ai="codex",
                model=str(model) if model else None,
                connection=req.connection,
                duration_s=out.duration_s,
                error_kind=ErrorKind.SERVER,
                error=clean_cli_text(out.stderr or out.stdout or f"Codex exited with code {out.exit_code}"),
                session_id=str(extracted_session_id) if extracted_session_id else None,
            )

        raw_data = {
            "usage": extracted_usage,
            "cost_usd": cost_usd,
        }

        parsed_json: Any = None
        if json_schema or (extracted_text.startswith("{") or extracted_text.startswith("[")):
            parsed_json = parse_json_or_none(extracted_text)

        return self.build_exec_result(
            ok=True,
            text=extracted_text,
            session_id=str(extracted_session_id) if extracted_session_id else None,
            ai="codex",
            model=str(model) if model else None,
            connection=req.connection,
            raw_data=raw_data,
            parsed_json=parsed_json,
            duration_s=out.duration_s,
        )
