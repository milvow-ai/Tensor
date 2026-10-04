"""The ``api`` executor: hands an :class:`ExecRequest` to the adapter of the connection's provider.

One adapter instance per provider is created on first use and kept (it owns an ``httpx.AsyncClient``);
``aclose`` closes them all. The adapters do the protocol work and classify every provider fault, so this class
only routes. A provider without an adapter is a configuration fault, reported as ``BAD_REQUEST`` (the
router counts that as neither a success nor a failure of the account and moves to the next candidate).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from farm.adapters import ADAPTERS
from farm.adapters._template import ApiAdapter
from farm.executors.base import ErrorKind, ExecRequest, ExecResult


class ApiExecutor:
    def __init__(
        self,
        adapters: Mapping[str, type[ApiAdapter]] | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._classes: Mapping[str, type[ApiAdapter]] = ADAPTERS if adapters is None else adapters
        self._clock = clock
        self._instances: dict[str, ApiAdapter] = {}

    def _adapter(self, provider_id: str) -> ApiAdapter | None:
        adapter = self._instances.get(provider_id)
        if adapter is None:
            cls = self._classes.get(provider_id)
            if cls is None:
                return None
            adapter = self._instances[provider_id] = cls(clock=self._clock)
        return adapter

    async def execute(self, req: ExecRequest) -> ExecResult:
        adapter = self._adapter(req.connection.provider_id)
        if adapter is None:
            return ExecResult(
                ok=False,
                error_kind=ErrorKind.BAD_REQUEST,
                error=(
                    f"no API adapter for provider '{req.connection.provider_id}' "
                    "(add one in farm/adapters or change the provider's executor)"
                ),
            )
        return await adapter.execute(req)

    async def aclose(self) -> None:
        instances, self._instances = list(self._instances.values()), {}
        for adapter in instances:
            await adapter.aclose()
