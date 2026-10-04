"""CLI agent executors for Harness Farm AI pool."""

from farm.executors.base import ErrorKind, ExecRequest, ExecResult, Executor
from farm.executors.cli_agent.agy import AgyCliExecutor
from farm.executors.cli_agent.base import (
    BaseCliAgentExecutor,
    SubprocessOutput,
    clean_cli_text,
    extract_cost_usd,
    extract_token_usage,
    parse_json_or_none,
    parse_jsonl,
    parse_reset_at,
    run_cli_process,
)
from farm.executors.cli_agent.claude import ClaudeCliExecutor
from farm.executors.cli_agent.codex import CodexCliExecutor
from farm.executors.cli_agent.hermes import HermesCliExecutor


class CliAgentExecutor:
    """Dispatches to the CLI agent executor based on connection.meta['cli'] or provider_id."""

    def __init__(self, drivers: dict[str, Executor] | None = None) -> None:
        if drivers is not None:
            self.drivers: dict[str, Executor] = drivers
        else:
            self.drivers = {
                "claude": ClaudeCliExecutor(),
                "codex": CodexCliExecutor(),
                "agy": AgyCliExecutor(),
                "gemini": AgyCliExecutor(),
                "hermes": HermesCliExecutor(),
            }

    async def execute(self, req: ExecRequest) -> ExecResult:
        meta = req.connection.meta or {}
        cli_name = meta.get("cli") or req.connection.provider_id
        driver = self.drivers.get(str(cli_name).lower())
        if driver is None:
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.UNKNOWN,
                error=f"unknown cli agent driver '{cli_name}'",
            )
        return await driver.execute(req)

    async def aclose(self) -> None:
        pass


__all__ = [
    "AgyCliExecutor",
    "BaseCliAgentExecutor",
    "ClaudeCliExecutor",
    "CliAgentExecutor",
    "CodexCliExecutor",
    "HermesCliExecutor",
    "SubprocessOutput",
    "clean_cli_text",
    "extract_cost_usd",
    "extract_token_usage",
    "parse_json_or_none",
    "parse_jsonl",
    "parse_reset_at",
    "run_cli_process",
]

