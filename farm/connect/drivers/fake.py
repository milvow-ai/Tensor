"""Scripted fake login driver for testing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from farm.connect.drivers.base import (
    Identity,
    LoginEvent,
    Success,
)


class FakeDriver:
    def __init__(
        self,
        events: list[LoginEvent | tuple[float, LoginEvent]],
        identity: Identity | None = None,
        models: list[str] | None = None,
        method: str = "device_code",
    ) -> None:
        self._scripted_events = events
        self._identity = identity
        self._models = models or []
        self.method = method
        self.submitted: list[str] = []
        self.cancelled: bool = False
        self.home: Path | None = None
        self.hint: str | None = None

    async def start(self, home: Path, hint: str | None = None) -> None:
        self.home = home
        self.hint = hint

    async def events(self) -> AsyncIterator[LoginEvent]:
        for item in self._scripted_events:
            if isinstance(item, tuple):
                delay, event = item
                if delay > 0:
                    await asyncio.sleep(delay)
            else:
                event = item

            if isinstance(event, Success) and self.home is not None:
                self.home.mkdir(parents=True, exist_ok=True)
                (self.home / ".login_success_marker").write_text("ok", encoding="utf-8")
            yield event

    async def submit(self, text: str) -> None:
        self.submitted.append(text)

    async def cancel(self) -> None:
        self.cancelled = True

    async def identity(self, home: Path) -> Identity | None:
        return self._identity

    async def models(self, home: Path) -> list[str]:
        return list(self._models)
