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

import re
from collections import Counter
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, get_args
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    ValidationError,
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
McpTransport = Literal["stdio", "http", "sse"]
McpAuth = Literal["none", "env", "oauth"]
McpExpose = Literal["direct", "discovery", "auto"]

MCP_CAPABILITY_PREFIX = "mcp:"
"""The capability of a pass-through provider is ``mcp:<provider id>``. The registry cannot declare it (the
name is not a slug); the gateway creates it when it serves the provider."""

_SECRET_SHAPES = re.compile(
    r"""
      sk-ant-[A-Za-z0-9_-]{10,}                  # Anthropic
    | sk-[A-Za-z0-9_-]{20,}                      # OpenAI / OpenRouter style
    | sk_(?:live|test)_[A-Za-z0-9]{16,}          # Stripe
    | gh[pousr]_[A-Za-z0-9]{20,}                 # GitHub tokens
    | github_pat_[A-Za-z0-9_]{20,}
    | glpat-[A-Za-z0-9_-]{16,}                   # GitLab
    | xox[abprs]-[A-Za-z0-9-]{10,}               # Slack
    | AKIA[0-9A-Z]{16}                           # AWS access key id
    | AIza[0-9A-Za-z_-]{30,}                     # Google API key
    | eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{8,}   # JWT
    | (?i:bearer\s+[A-Za-z0-9._~+/=-]{16,})
    | (?i:(?:^|[\s?&;-])[a-z0-9_-]*              # NAME=value where the name ends in a credential word
          (?:token|api[_-]?key|apikey|secret|passw(?:or)?d|credential|auth(?:orization)?)
          =(?!\d+(?:[\s&#]|$))[^\s&#]{6,})
    | (?i:[?&;]key=[^\s&#]{6,})
    | ://[^/\s:@]+:[^/\s@]+@                     # user:password@host
    """,
    re.VERBOSE,
)


def looks_like_secret(text: str) -> bool:
    """True for a string shaped like a key, token, JWT, bearer credential or ``user:password@`` URL.

    A heuristic for the fields that cannot hold an ``env:`` reference (``args``, ``url``, ``meta``, ...): the
    registry refuses such a string instead of storing a credential next to the configuration.
    """
    return _SECRET_SHAPES.search(text) is not None


_ENV_REF = re.compile(r"^env:[A-Za-z_][A-Za-z0-9_]*$")

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$", max_length=63)]
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


def _env_ref(value: str) -> str:
    if not _ENV_REF.fullmatch(value):
        raise ValueError(
            "must be an 'env:NAME' reference to a variable in the local .env, never the value itself"
        )
    return value


def _mcp_url(value: str) -> str:
    if _ENV_REF.fullmatch(value):
        return value  # the whole URL lives in .env because it carries a credential
    try:
        parts = urlsplit(value)
        has_userinfo = parts.username is not None or parts.password is not None
    except ValueError:
        raise ValueError("url is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("url must be an http(s) URL, or an 'env:NAME' reference to one")
    if has_userinfo or looks_like_secret(value):
        raise ValueError("the url carries a credential: keep the whole URL in .env and write url: env:NAME")
    return value


EnvRef = Annotated[str, AfterValidator(_env_ref)]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", max_length=128)]
HeaderName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$", max_length=128)]
GlobPattern = Annotated[str, StringConstraints(min_length=1, max_length=200)]
McpUrl = Annotated[str, AfterValidator(_mcp_url)]


class McpToolsSpec(_Model):
    """Which of the server's tools the Farm exposes: the ``allow`` globs minus the ``deny`` globs."""

    allow: list[GlobPattern] = Field(default_factory=lambda: ["*"], min_length=1)
    deny: list[GlobPattern] = Field(default_factory=list)


class McpProviderSpec(_Model):
    """The ``mcp:`` block of a provider: how to reach one MCP server and how the Farm presents its tools.

    Credentials are never written here. ``env`` and ``headers`` values are ``env:NAME`` references to
    variables of the local ``.env`` (``farm set-secret NAME``); ``url`` may be one when the URL itself is the
    secret. ``auth`` says where the account's own credential comes from: ``none``; ``env`` (the connection's
    ``auth_ref`` ``env:NAME`` is sent as a bearer token); ``oauth`` (the connection's ``auth_ref``
    ``token-store:ID`` holds the tokens of one login). Each connection may add or replace ``env`` /
    ``headers`` entries in ``meta.mcp`` (see :class:`McpConnectionOverride`).
    """

    transport: McpTransport = "stdio"
    command: str | None = Field(default=None, description="stdio: the program to start")
    args: list[str] = Field(default_factory=list)
    cwd: str | None = None
    env: dict[EnvName, EnvRef] = Field(default_factory=dict, description="stdio: variable -> env:NAME")
    url: McpUrl | None = Field(default=None, description="http / sse: the server's endpoint")
    headers: dict[HeaderName, EnvRef] = Field(
        default_factory=dict, description="http / sse: header -> env:NAME"
    )
    auth: McpAuth = "none"
    namespace: Slug | None = Field(
        default=None,
        description="Prefix of the exposed tool names (<namespace>__<tool>); default: the provider id",
    )
    tools: McpToolsSpec = Field(default_factory=McpToolsSpec)
    expose: McpExpose = Field(
        default="auto",
        description="direct: every tool is its own tool; discovery: search_tools + call_tool only; "
        "auto: direct while the whole Farm exposes at most settings.mcp_direct_limit tools",
    )
    timeout_s: float | None = Field(
        default=None, gt=0, le=3600, description="Per-attempt timeout of a call (default: the Farm's, 30 s)"
    )

    @model_validator(mode="after")
    def _coherent(self) -> McpProviderSpec:
        problems: list[str] = []
        if self.transport == "stdio":
            if not (self.command and self.command.strip()):
                problems.append("a stdio server needs a command")
            if self.url or self.headers:
                problems.append("url and headers belong to http / sse servers")
            if self.auth != "none":
                problems.append(f"auth '{self.auth}' needs an http / sse server (a stdio server takes env)")
        else:
            if not self.url:
                problems.append(f"a {self.transport} server needs a url")
            if self.command or self.args or self.cwd or self.env:
                problems.append("command, args, cwd and env belong to stdio servers")
        for text in (self.command, self.cwd, *self.args):
            if text and looks_like_secret(text):
                problems.append("command, args or cwd hold something shaped like a credential: use env")
                break
        if problems:
            raise ValueError("; ".join(problems))
        return self


class McpConnectionOverride(_Model):
    """What one connection changes about its provider's server (``connection.meta['mcp']``)."""

    env: dict[EnvName, EnvRef] = Field(default_factory=dict)
    headers: dict[HeaderName, EnvRef] = Field(default_factory=dict)


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

    @field_validator("meta")
    @classmethod
    def _validate_meta(cls, meta: dict[str, Any]) -> dict[str, Any]:
        if "mcp" in meta:
            try:
                meta = {**meta, "mcp": McpConnectionOverride.model_validate(meta["mcp"]).model_dump()}
            except ValidationError as exc:
                problems = "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                    for e in exc.errors(include_input=False, include_url=False)
                )
                raise ValueError(f"meta.mcp is invalid ({problems})") from None
        if "allow_edit" in meta and not isinstance(meta["allow_edit"], bool):
            raise ValueError("meta.allow_edit must be a strict boolean (True or False)")
        if "edit_roots" in meta:
            roots = meta["edit_roots"]
            if isinstance(roots, (str, Path)):
                raw_roots = [roots]
            elif isinstance(roots, list):
                raw_roots = roots
            else:
                raise ValueError("meta.edit_roots must be a list of paths")
            for r in raw_roots:
                r_str = str(r).strip()
                if not r_str or r_str == ".":
                    raise ValueError(f"edit_roots entry cannot be empty or '.': {r!r}")
                if not Path(r_str).is_absolute():
                    raise ValueError(f"edit_roots entry must be an absolute path: {r!r}")
        return meta

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
    mcp: McpProviderSpec | None = Field(
        default=None,
        exclude=True,
        description="MCP pass-through server (executor: mcp). Stored in config.mcp, which is what the "
        "database and the gateway read; this field is the typed view of it.",
    )
    connections: list[ConnectionSpec] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _mcp_block_lives_in_config(cls, data: Any) -> Any:
        """``mcp:`` is accepted next to ``config:`` and kept in ``config.mcp`` (what the database holds)."""
        if isinstance(data, dict) and "mcp" in data:
            data = dict(data)
            block = data.pop("mcp")
            config = dict(data.get("config") or {})
            if "mcp" in config:
                raise ValueError("the mcp block is given twice (mcp: and config.mcp)")
            config["mcp"] = block
            data["config"] = config
        return data

    @model_validator(mode="after")
    def _typed_mcp(self) -> ProviderSpec:
        block = self.config.get("mcp")
        if block is None:
            return self
        if self.executor != "mcp":
            raise ValueError("an mcp block needs executor: mcp")
        try:
            spec = block if isinstance(block, McpProviderSpec) else McpProviderSpec.model_validate(block)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or '<block>'}: {str(e['msg']).removeprefix('Value error, ')}"
                for e in exc.errors(include_input=False, include_url=False)
            )
            raise ValueError(f"mcp: {problems}") from None
        self.config["mcp"] = spec.model_dump(mode="json")
        if spec.timeout_s is not None:
            if self.config.get("timeout_s", spec.timeout_s) != spec.timeout_s:
                raise ValueError("timeout_s is set in config and in mcp with different values")
            self.config["timeout_s"] = spec.timeout_s  # the router reads the per-attempt timeout from here
        self.mcp = spec
        return self


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
    mcp_direct_limit: int = Field(
        default=40,
        ge=1,
        le=1000,
        description="expose: auto shows every MCP tool as its own tool while there are at most this many",
    )
    mcp_pinned: list[GlobPattern] = Field(
        default_factory=list,
        description="Exposed MCP tool names (globs allowed) that stay their own tool under discovery",
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


def _secret_paths(value: Any, path: str = "") -> Iterator[str]:
    """Dotted paths of the strings in ``value`` that look like a credential (never the strings themselves)."""
    if isinstance(value, str):
        if looks_like_secret(value):
            yield path
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _secret_paths(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _secret_paths(item, f"{path}.{index}")


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

        problems.extend(self._mcp_problems())
        for path in _secret_paths(self.model_dump(mode="json")):
            problems.append(
                f"{path}: looks like a literal key or token; keep it in .env and reference it as env:NAME"
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

    def _mcp_problems(self) -> list[str]:
        """Rules that span the MCP providers: one namespace each, and credentials that fit ``mcp.auth``."""
        problems: list[str] = []
        namespaces: dict[str, str] = {}
        for provider_id, provider in self.providers.items():
            spec = provider.mcp
            if spec is None:
                continue
            namespace = spec.namespace or provider_id
            if namespace in namespaces:
                problems.append(
                    f"providers '{namespaces[namespace]}' and '{provider_id}' share the MCP namespace "
                    f"'{namespace}' (set mcp.namespace on one of them)"
                )
            else:
                namespaces[namespace] = provider_id
            needed = {"env": "env", "oauth": "token-store"}.get(spec.auth)
            for connection in provider.connections:
                if needed is not None and not connection.auth_ref.startswith(f"{needed}:"):
                    problems.append(
                        f"provider '{provider_id}': mcp.auth '{spec.auth}' needs auth_ref '{needed}:...' "
                        f"on connection '{connection.id}'"
                    )
        return problems

    @model_validator(mode="after")
    def _mcp_default_units(self) -> Registry:
        """An MCP connection without units still counts its calls (quota unit ``calls``, unlimited)."""
        for provider in self.providers.values():
            if provider.mcp is None:
                continue
            for connection in provider.connections:
                if not connection.units:
                    connection.units["calls"] = UnitSpec(limit=None, period="none", charged_on="success")
        return self
