"""Base class for REST API adapters (HANDOFF section 4.6: a 50-100 line adapter per provider).

A provider adapter supplies: ``provider_id``, ``base_url``, ``capabilities()`` (capability name ->
async handler) and, if the provider has a balance endpoint, ``_fetch_balance``. Everything fragile lives
here once:

* the secret is resolved from the connection's ``auth_ref`` at call time and never leaves this module
  except inside the outgoing request;
* every provider fault (HTTP status, transport error, timeout, malformed JSON, an error hidden in a 200
  body) becomes a classified :class:`~farm.executors.base.ExecResult`; only programming errors raise;
* every error string is passed through :func:`farm.secrets.redact`, exceptions from httpx are never
  chained (their text may contain the request URL) and the ``httpx`` logger, which prints full URLs at
  INFO, is filtered.

Added in M2c: ``_request_json`` (any method, per-call headers such as ``x-api-key``, optional JSON body,
``Accept: application/json`` on every request), ``requires_auth = False`` for keyless providers,
``parse_params`` (input validation -> BAD_REQUEST) and ``empty_result`` (the provider answered "nothing":
``ok=True, found=False, error_kind=EMPTY``, with whatever the provider really charged).

Status -> ErrorKind (``classify_status``): 429 RATE_LIMITED (+Retry-After), 401/403 AUTH,
402 LIMIT_REACHED, 404 EMPTY, 408 TIMEOUT, other 4xx BAD_REQUEST, 5xx SERVER, anything else non-2xx
UNKNOWN; transport timeout TIMEOUT, other transport failure SERVER.
"""

from __future__ import annotations

import math
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, ClassVar, Self

import httpx
import structlog
from pydantic import BaseModel, Field, ValidationError

import farm
from farm.capabilities.schemas import Source
from farm.executors.base import ConnectionView, ErrorKind, ExecRequest, ExecResult
from farm.secrets import AuthRefError, install_log_redaction, redact, resolve_auth

# httpx logs "HTTP Request: GET <full url>" at INFO; the full URL carries the API key for query-string
# providers. Redact at the logger so no log configuration downstream can leak it.
install_log_redaction("httpx")

log = structlog.get_logger(__name__)

Handler = Callable[[ExecRequest, str], Awaitable[ExecResult]]

# Query-string values httpx can encode; a sequence value becomes a repeated key (``titles[]=a&titles[]=b``).
QueryParams = Mapping[str, str | int | float | bool | Sequence[str]]

MAX_ERROR_CHARS = 300


def clip(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """Redacted, single-line, length-bounded version of provider-supplied text (errors and logs).

    Redaction comes first on purpose: cutting first could slice a key in half, and a half key is no
    longer recognisable (so it would not be masked).
    """
    flat = " ".join(redact(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


class ProviderError(Exception):
    """A classified provider fault, raised inside an adapter and turned into an ``ExecResult`` at its edge.

    The message is redacted at construction so even an uncaught traceback cannot show a key.
    """

    def __init__(
        self,
        kind: ErrorKind,
        message: str,
        *,
        retry_after_s: float | None = None,
        reset_at: datetime | None = None,
        tag: str | None = None,
    ) -> None:
        super().__init__(clip(message))
        self.kind = kind
        self.retry_after_s = retry_after_s
        self.reset_at = reset_at
        self.tag = tag  # lets a provider-specific handler recognise a particular situation


class BalanceResult(BaseModel):
    """Outcome of a balance lookup. ``units`` maps consumption-unit name -> remaining amount."""

    ok: bool
    remaining: float | None = None  # remaining amount of the adapter's primary unit (``credits``)
    units: dict[str, float] = Field(default_factory=dict)
    error_kind: ErrorKind | None = None
    error: str | None = None
    retry_after_s: float | None = None
    latency_ms: int = 0


def classify_status(status_code: int) -> ErrorKind | None:
    """The generic HTTP status -> ErrorKind map. ``None`` means 2xx (not an error)."""
    if 200 <= status_code < 300:
        return None
    if status_code == 429:
        return ErrorKind.RATE_LIMITED
    if status_code in (401, 403):
        return ErrorKind.AUTH
    if status_code == 402:
        return ErrorKind.LIMIT_REACHED
    if status_code == 404:
        return ErrorKind.EMPTY
    if status_code == 408:
        return ErrorKind.TIMEOUT
    if 400 <= status_code < 500:
        return ErrorKind.BAD_REQUEST
    if status_code >= 500:
        return ErrorKind.SERVER
    return ErrorKind.UNKNOWN  # 1xx / 3xx: we never follow redirects, so a redirect is a misconfiguration


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """``Retry-After`` as seconds from now: delta-seconds or an HTTP date. ``None`` if absent or garbage."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def as_float(value: object) -> float | None:
    """A finite number from a JSON value (int, float or numeric string); ``None`` for anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def as_int(value: object) -> int | None:
    """A whole number from a JSON value (``None`` for anything that is not finite)."""
    number = as_float(value)
    return None if number is None else int(number)


def as_text(value: object) -> str | None:
    """A stripped, non-empty string from a JSON value; ``None`` for everything else."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def as_dict(value: object) -> dict[str, Any]:
    """``value`` if it is a JSON object, else an empty one (so ``.get`` chains need no isinstance dance)."""
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def parse_params[M: BaseModel](model: type[M], params: Mapping[str, Any], capability: str) -> M:
    """Validate ``ExecRequest.params`` against a capability input model; a bad input is a BAD_REQUEST.

    The message lists what is wrong without echoing the offending values (they may be secrets pasted into
    the wrong field).
    """
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        problems = "; ".join(str(e["msg"]) for e in exc.errors(include_input=False))
        raise ProviderError(ErrorKind.BAD_REQUEST, f"invalid {capability} params: {problems}") from None


def empty_result(
    data: BaseModel, reason: str, *, units_used: Mapping[str, float] | None = None
) -> ExecResult:
    """The provider answered, and the answer is "nothing found".

    ``ok`` stays true because the call itself worked (and may have been charged: ``units_used`` carries the
    provider's real charge, 0 for free lookups); ``found=False`` + ``EMPTY`` tell the router to try the next
    pool without counting a health failure. ``data`` is the normalised, empty output so a caller (or the
    cache) can still see which question was asked and which provider answered it.
    """
    return ExecResult(
        ok=True,
        data=data.model_dump(mode="json"),
        found=False,
        units_used=dict(units_used or {}),
        error_kind=ErrorKind.EMPTY,
        error=clip(reason),
    )


class ApiAdapter(ABC):
    """One REST provider. Subclass, set ``provider_id``/``base_url``, implement ``capabilities``."""

    provider_id: ClassVar[str]
    base_url: ClassVar[str]
    balance_unit: ClassVar[str] = "credits"
    # False for providers that need no key (public job boards): the handler then gets an empty secret and
    # the connection's ``auth_ref`` is never resolved.
    requires_auth: ClassVar[bool] = True

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """``client`` is injectable (tests, shared connection pools); otherwise one is created lazily.

        ``base_url`` overrides the class default (e.g. a regional endpoint). It is deliberately a
        constructor argument and never read from per-request data, so a request can't redirect the key.
        """
        self._client = client
        self._owns_client = client is None
        self._base_url = (base_url or self.base_url).rstrip("/")
        self._clock = clock or (lambda: datetime.now(UTC))

    # -- lifecycle ---------------------------------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                headers={"Accept": "application/json", "User-Agent": f"harness-farm/{farm.__version__}"},
            )
        return self._client

    async def aclose(self) -> None:
        """Close the HTTP client if this adapter created it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # -- what subclasses provide -------------------------------------------------------------------

    @abstractmethod
    def capabilities(self) -> Mapping[str, Handler]:
        """capability name -> ``async handler(req, secret) -> ExecResult`` (raise ProviderError on faults)."""

    async def _fetch_balance(self, secret: str, timeout_s: float) -> dict[str, float]:
        """Remaining amount per consumption unit. Override when the provider has a balance endpoint."""
        raise ProviderError(ErrorKind.BAD_REQUEST, f"{self.provider_id} has no balance endpoint")

    def _error_from_response(
        self, status_code: int, body: Any, retry_after_s: float | None
    ) -> ProviderError | None:
        """Turn an HTTP response into a ProviderError, or ``None`` if it is a success.

        The default is the pure status map. Providers override this to also look inside the body (many
        report failures as HTTP 200 + an error JSON); they should start from ``super()`` so the status
        semantics stay in one place.
        """
        kind = classify_status(status_code)
        if kind is None:
            return None
        return ProviderError(kind, f"HTTP {status_code}", retry_after_s=retry_after_s)

    # -- shared plumbing ---------------------------------------------------------------------------

    def _now(self) -> datetime:
        return self._clock()

    def _source(self, req: ExecRequest) -> Source:
        """Provenance for an output model: this provider pool and the connection that served ``req``."""
        return Source(provider=self.provider_id, connection_id=req.connection.id)

    async def _get_json(
        self,
        path: str,
        *,
        params: QueryParams,
        timeout_s: float,
        headers: Mapping[str, str] | None = None,
        base_url: str | None = None,
    ) -> Any:
        """GET ``base_url + path``; return parsed JSON or raise a classified ``ProviderError``."""
        return await self._request_json(
            "GET", path, params=params, timeout_s=timeout_s, headers=headers, base_url=base_url
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        timeout_s: float,
        params: QueryParams | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Any = None,
        base_url: str | None = None,
    ) -> Any:
        """Send one request; return the parsed JSON body or raise a classified ``ProviderError``.

        ``headers`` carries per-call credentials (``x-api-key`` style), which keeps them out of the URL.
        ``base_url`` exists for adapters that talk to several fixed hosts (the public job boards): it must
        be a constant in the adapter's code, never a value taken from a request or a connection.
        """
        url = f"{(base_url or self._base_url).rstrip('/')}{path}"
        # Per request, not only as a client default: an injected shared client has no such default, and
        # some providers (Adzuna) answer JSONP unless the Accept header asks for JSON.
        try:
            response = await self._http().request(
                method,
                url,
                params=params,
                headers={"Accept": "application/json", **(headers or {})},
                json=json_body,
                timeout=httpx.Timeout(timeout_s),
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise ProviderError(ErrorKind.TIMEOUT, f"no response within {timeout_s:g}s") from None
        except httpx.RequestError as exc:
            # Class name only: the message of an httpx error can contain the request URL (and the key).
            raise ProviderError(ErrorKind.SERVER, f"provider unreachable ({type(exc).__name__})") from None

        retry_after = parse_retry_after(response.headers.get("retry-after"), now=self._now())
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = None

        error = self._error_from_response(response.status_code, body, retry_after)
        if error is not None:
            raise error
        if body is None:
            raise ProviderError(ErrorKind.UNKNOWN, f"malformed JSON in HTTP {response.status_code} response")
        return body

    def _failure(self, error: ProviderError) -> ExecResult:
        return ExecResult(
            ok=False,
            error_kind=error.kind,
            error=redact(f"{self.provider_id}: {error}"),
            retry_after_s=error.retry_after_s,
            reset_at=error.reset_at,
        )

    # -- public API --------------------------------------------------------------------------------

    async def execute(self, req: ExecRequest) -> ExecResult:
        """Run ``req.capability`` on ``req.connection``. Never raises for provider errors."""
        started = time.perf_counter()
        result = await self._execute(req)
        result = result.model_copy(update={"latency_ms": int((time.perf_counter() - started) * 1000)})
        if not result.ok:
            log.warning(
                "adapter.failure",
                provider=self.provider_id,
                connection=req.connection.id,
                capability=req.capability,
                error_kind=result.error_kind,
                error=result.error,
            )
        return result

    async def _execute(self, req: ExecRequest) -> ExecResult:
        handler = self.capabilities().get(req.capability)
        if handler is None:
            return self._failure(
                ProviderError(ErrorKind.BAD_REQUEST, f"does not support capability '{req.capability}'")
            )
        secret = ""
        if self.requires_auth:
            try:
                secret = resolve_auth(req.connection.auth_ref)
            except AuthRefError as exc:
                return self._failure(ProviderError(ErrorKind.AUTH, str(exc)))
        try:
            return await handler(req, secret)
        except ProviderError as exc:
            return self._failure(exc)

    async def balance(self, connection: ConnectionView, *, timeout_s: float = 15.0) -> BalanceResult:
        """Remaining quota at the provider. Never raises for provider errors."""
        started = time.perf_counter()
        try:
            units = await self._fetch_balance(resolve_auth(connection.auth_ref), timeout_s)
            result = BalanceResult(ok=True, remaining=units.get(self.balance_unit), units=units)
        except AuthRefError as exc:
            result = BalanceResult(
                ok=False, error_kind=ErrorKind.AUTH, error=redact(f"{self.provider_id}: {exc}")
            )
        except ProviderError as exc:
            result = BalanceResult(
                ok=False,
                error_kind=exc.kind,
                error=redact(f"{self.provider_id}: {exc}"),
                retry_after_s=exc.retry_after_s,
            )
        return result.model_copy(update={"latency_ms": int((time.perf_counter() - started) * 1000)})
