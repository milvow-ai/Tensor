"""Registry models: YAML -> Pydantic -> JSON Schema (the Console generates its forms from the schema).

Shape: briefs/CONTEXT.md section 4. The DB is the runtime source of truth; ``config/registry.yaml`` seeds
it. Design choices worth knowing:

* every model forbids unknown keys, so a typo in the YAML (``chaged_on``) is an error, not a silent default;
* validation errors never echo the offending input (``hide_input_in_errors``): someone may have pasted a
  raw key into ``auth_ref``;
* ``Money``/``Quantity`` are ``Decimal`` in Python and plain JSON numbers in the exported schema and in
  ``model_dump(mode="json")``, which is what a form needs;
* cross-references (unique connection ids, routes -> providers, capability kind == provider kind,
  budget refs) are checked once, in ``Registry``, and reported together.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
    field_validator,
    model_validator,
)

from farm.secrets import AuthRefError, parse_auth_ref

Strategy = Literal[
    "failover", "most_remaining", "round_robin", "parallel_split", "sticky", "fit_check", "pin"
]
STRATEGIES: tuple[str, ...] = get_args(Strategy)

ProviderKind = Literal["tool", "ai"]
ExecutorKind = Literal["api", "mcp", "llm", "cli_agent", "agent", "browser", "local", "human"]
ConnectionStatus = Literal["active", "paused", "needs_login", "exhausted", "disabled"]
Period = Literal["minute", "hour", "day", "week", "month", "rolling_5h", "total", "none"]
ChargedOn = Literal["attempt", "success", "found"]

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$", max_length=64)]
Scope = Annotated[str, StringConstraints(pattern=r"^(internal|client:[A-Za-z0-9][A-Za-z0-9_.-]*)$")]

_NUMBER_SCHEMA: dict[str, Any] = {"type": "number", "minimum": 0}
Money = Annotated[
    Decimal,
    Field(ge=0),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema(_NUMBER_SCHEMA),
]
Quantity = Annotated[
    Decimal,
    Field(ge=0),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema(_NUMBER_SCHEMA),
]


def _decimal_default(value: int) -> Any:
    """A Decimal default that the exported schema shows as the plain number a form input needs."""
    return Field(default=Decimal(value), json_schema_extra={"default": value})


def _check_auth_ref(value: str) -> str:
    try:
        parse_auth_ref(value)
    except AuthRefError as exc:
        raise ValueError(str(exc)) from None  # message is guaranteed to omit the value
    return value


AuthRef = Annotated[str, AfterValidator(_check_auth_ref)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class PlanSpec(_Model):
    name: str
    price_usd: Money = _decimal_default(0)
    billing_day: int | None = Field(default=None, ge=1, le=31)
    renews_on: date | None = None


class UnitSpec(_Model):
    """One consumption unit of a connection (credits, requests, ...). ``limit`` null = unlimited."""

    limit: Quantity | None = None
    period: Period = "none"
    anchor: int | None = Field(
        default=None, ge=1, le=31, description="Day of month the period resets (month only)"
    )
    charged_on: ChargedOn = "attempt"
    unit_cost_usd: Money = _decimal_default(0)
    estimate_per_call: Quantity = _decimal_default(1)

    @model_validator(mode="after")
    def _anchor_only_for_months(self) -> UnitSpec:
        if self.anchor is not None and self.period != "month":
            raise ValueError("anchor (day of month) only applies to period 'month'")
        return self


class ConnectionSpec(_Model):
    id: Slug
    label: str = ""
    auth_ref: AuthRef
    scope: list[Scope] = Field(default_factory=lambda: ["internal"], min_length=1)
    priority: int = Field(default=100, ge=0, description="Lower is tried first")
    strategy: Strategy | None = Field(default=None, description="Overrides the provider's strategy")
    concurrency: int = Field(default=1, ge=1)
    rate_per_min: int | None = Field(default=None, ge=1)
    status: ConnectionStatus = "active"
    plan: PlanSpec | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    units: dict[Slug, UnitSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _default_label(self) -> ConnectionSpec:
        if not self.label:
            self.label = self.id
        return self


class ProviderSpec(_Model):
    name: str = ""
    kind: ProviderKind
    executor: ExecutorKind
    default_strategy: Strategy = "failover"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    connections: list[ConnectionSpec] = Field(default_factory=list)


class CapabilitySpec(_Model):
    kind: ProviderKind
    description: str = ""
    routes: list[Slug] = Field(min_length=1, description="Provider ids, in router order")
    strategy: Strategy = "failover"
    cache_ttl_seconds: int = Field(default=0, ge=0)

    @field_validator("routes")
    @classmethod
    def _routes_unique(cls, routes: list[str]) -> list[str]:
        repeated = sorted(r for r, n in Counter(routes).items() if n > 1)
        if repeated:
            raise ValueError(f"routes lists the same provider more than once: {', '.join(repeated)}")
        return routes


class BudgetSpec(_Model):
    """Monthly USD caps. 0 means "no paid spend allowed"; free calls (cost 0) are unaffected."""

    global_monthly_usd: Money = _decimal_default(0)
    per_provider: dict[Slug, Money] = Field(default_factory=dict)
    per_connection: dict[Slug, Money] = Field(default_factory=dict)
    hard_stop: bool = True


class SettingsSpec(_Model):
    owner_email: Annotated[str, StringConstraints(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)]
    alert_thresholds: list[Annotated[int, Field(ge=1, le=1000)]] = Field(
        default_factory=lambda: [50, 80, 100]
    )
    timezone: str = "UTC"
    global_monthly_budget_usd: Money | None = Field(
        default=None, description="Mirror of budgets.global_monthly_usd (filled in if omitted)"
    )

    @field_validator("alert_thresholds")
    @classmethod
    def _sorted_unique(cls, values: list[int]) -> list[int]:
        return sorted(set(values))

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, name: str) -> str:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError("timezone must be an IANA name such as 'UTC' or 'Europe/Berlin'") from None
        return name


class Registry(_Model):
    providers: dict[Slug, ProviderSpec]
    capabilities: dict[Slug, CapabilitySpec]
    budgets: BudgetSpec = Field(default_factory=BudgetSpec)
    settings: SettingsSpec

    def iter_connections(self) -> Iterator[tuple[str, ConnectionSpec]]:
        """``(provider_id, connection)`` for every connection."""
        for provider_id, provider in self.providers.items():
            for connection in provider.connections:
                yield provider_id, connection

    @model_validator(mode="after")
    def _cross_references(self) -> Registry:
        problems: list[str] = []

        owners: dict[str, str] = {}
        for provider_id, connection in self.iter_connections():
            if connection.id in owners:
                where = (
                    f"provider '{provider_id}'"
                    if owners[connection.id] == provider_id
                    else f"providers '{owners[connection.id]}' and '{provider_id}'"
                )
                problems.append(f"connection id '{connection.id}' is used more than once ({where})")
            else:
                owners[connection.id] = provider_id

        for name, capability in self.capabilities.items():
            for route in capability.routes:
                provider = self.providers.get(route)
                if provider is None:
                    problems.append(
                        f"capability '{name}': route '{route}' is not a provider in this registry"
                    )
                elif provider.kind != capability.kind:
                    problems.append(
                        f"capability '{name}' is kind '{capability.kind}' but route '{route}' "
                        f"is a '{provider.kind}' provider"
                    )

        for provider_id in self.budgets.per_provider:
            if provider_id not in self.providers:
                problems.append(f"budgets.per_provider: '{provider_id}' is not a provider")
        for connection_id in self.budgets.per_connection:
            if connection_id not in owners:
                problems.append(f"budgets.per_connection: '{connection_id}' is not a connection")

        mirrored = self.settings.global_monthly_budget_usd
        if mirrored is not None and mirrored != self.budgets.global_monthly_usd:
            problems.append(
                "settings.global_monthly_budget_usd and budgets.global_monthly_usd disagree "
                "(set one of them, or make them equal)"
            )

        if problems:
            raise ValueError("registry cross-reference errors:\n  - " + "\n  - ".join(problems))

        self.settings.global_monthly_budget_usd = self.budgets.global_monthly_usd
        for provider_id, provider in self.providers.items():
            if not provider.name:
                provider.name = provider_id
        return self
