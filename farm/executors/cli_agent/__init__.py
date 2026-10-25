"""CLI agent executors for Harness Farm AI pool."""

from collections.abc import Awaitable, Callable

from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult, Executor
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
from farm.executors.cli_agent.gemini_cli import GeminiCliExecutor
from farm.executors.cli_agent.hermes import HermesCliExecutor
from farm.executors.cli_agent.perplexity import PerplexityApiExecutor


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
                "gemini-cli": GeminiCliExecutor(),
                "hermes": HermesCliExecutor(),
                "perplexity": PerplexityApiExecutor(),
            }

    def _driver(self, connection: ConnectionView) -> tuple[str, Executor | None]:
        cli_name = str((connection.meta or {}).get("cli") or connection.provider_id)
        return cli_name, self.drivers.get(cli_name.lower())

    async def execute(self, req: ExecRequest) -> ExecResult:
        cli_name, driver = self._driver(req.connection)
        if driver is None:
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.UNKNOWN,
                error=f"unknown cli agent driver '{cli_name}'",
            )
        return await driver.execute(req)

    async def list_models(self, connection: ConnectionView) -> list[str] | None:
        """The model ids the account's CLI accepts, when that CLI can list them (agy); ``None`` otherwise."""
        _, driver = self._driver(connection)
        lister: Callable[[ConnectionView], Awaitable[list[str] | None]] | None = getattr(
            driver, "list_models", None
        )
        return None if lister is None else await lister(connection)

    async def aclose(self) -> None:
        pass


__all__ = [
    "AgyCliExecutor",
    "BaseCliAgentExecutor",
    "ClaudeCliExecutor",
    "CliAgentExecutor",
    "CodexCliExecutor",
    "GeminiCliExecutor",
    "HermesCliExecutor",
    "PerplexityApiExecutor",
    "SubprocessOutput",
    "clean_cli_text",
    "extract_cost_usd",
    "extract_token_usage",
    "parse_json_or_none",
    "parse_jsonl",
    "parse_reset_at",
    "run_cli_process",
]

