"""Base classes, events, and protocols for login drivers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Url:
    url: str


@dataclass(frozen=True)
class UserCode:
    code: str
    verification_uri: str


@dataclass(frozen=True)
class NeedsInput:
    hint: str = ""


@dataclass(frozen=True)
class NeedsVerification:
    url: str
    message: str


@dataclass(frozen=True)
class Success:
    pass


@dataclass(frozen=True)
class Failure:
    kind: str
    excerpt: str


type LoginEvent = Url | UserCode | NeedsInput | NeedsVerification | Success | Failure


@dataclass
class Identity:
    provider_account_id: str
    email: str
    plan: str | None = None
    org: str | None = None

    def masked_email(self) -> str:
        """Return masked email: first character + ••••• + @domain."""
        if not self.email or "@" not in self.email:
            return self.email or ""
        local, domain = self.email.split("@", 1)
        first = local[0] if local else ""
        return f"{first}\u2022\u2022\u2022\u2022\u2022@{domain}"


@runtime_checkable
class LoginDriver(Protocol):
    method: str

    async def start(self, home: Path, hint: str | None = None) -> None: ...

    def events(self) -> AsyncIterator[LoginEvent]: ...

    async def submit(self, text: str) -> None: ...

    async def cancel(self) -> None: ...

    async def identity(self, home: Path) -> Identity | None: ...

    async def models(self, home: Path) -> list[str]: ...
