"""ZeroBounce API v2: ``verify_email`` (``/v2/validate``) and the credit balance (``/v2/getcredits``).

Docs (fetched 2026-10-04):
  https://www.zerobounce.net/docs/email-validation-api-quickstart/v2-validate-emails
  https://www.zerobounce.net/docs/email-validation-api-quickstart/v2-credit-balance
  https://www.zerobounce.net/docs/email-validation-api-quickstart/v2-status-codes

* The key travels as the ``api_key`` query parameter (GET). The docs say POST with a form body is also
  accepted since 2026-09-03, which would keep the key out of URLs entirely; GET is used because it is
  the long-established form and POST could not be exercised offline (see the M1b report).
* Charging: every verdict costs one credit except ``unknown``, "never charged" per the docs. So
  ``units_used`` is ``{"credits": 1}`` for a definitive status and ``{}`` for ``unknown`` (``found=False``).
* The documented failure body is ``{"error": "Invalid API Key or your account ran out of credits"}``:
  one message for two very different faults. ``getcredits`` is free, so on that message the adapter asks
  it which one it is (-1 means a bad key; 0 means out of credits). If the probe itself fails the result
  stays ``AUTH``, the cautious reading: a person must look at the key either way.
* ZeroBounce answers within 30 s by default and returns ``unknown`` if it runs out of time. The adapter
  sends ``timeout`` = ``timeout_s - 3`` (3..60) so ZeroBounce gives up before our HTTP client does.
* Rate limit: a burst over 80,000 requests / 10 s earns a one-minute block, reported as ``RATE_LIMITED``
  with ``retry_after_s`` = the ``Retry-After`` header, else 60.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from farm.adapters._template import ApiAdapter, Handler, ProviderError, as_float, clip
from farm.capabilities.schemas import VerifyEmailIn, VerifyEmailOut, is_definitive, map_zerobounce_status
from farm.executors.base import ErrorKind, ExecRequest, ExecResult

RATE_LIMIT_BLOCK_S = 60.0
_KEY_OR_CREDITS = "key_or_credits"

# HTTP statuses that already say what is wrong. 401/403 are *not* here: ZeroBounce's one shared
# "invalid key or no credits" message may arrive with either, and then only the balance can tell.
_HTTP_AUTHORITATIVE = {
    ErrorKind.RATE_LIMITED,
    ErrorKind.LIMIT_REACHED,
    ErrorKind.SERVER,
    ErrorKind.TIMEOUT,
}


def _error_text(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    text = body.get("error") or body.get("message")
    return clip(text) if isinstance(text, str) and text.strip() else None


def _api_timeout(timeout_s: float) -> int:
    return int(max(3, min(60, timeout_s - 3)))


class ZeroBounceAdapter(ApiAdapter):
    provider_id = "zerobounce"
    base_url = "https://api.zerobounce.net"

    def capabilities(self) -> Mapping[str, Handler]:
        return {"verify_email": self._verify_email}

    def _error_from_response(
        self, status_code: int, body: Any, retry_after_s: float | None
    ) -> ProviderError | None:
        if status_code == 429 and retry_after_s is None:
            retry_after_s = RATE_LIMIT_BLOCK_S
        by_status = super()._error_from_response(status_code, body, retry_after_s)
        message = _error_text(body)
        if message is None:
            return by_status
        detail = f"HTTP {status_code}: {message}"
        if by_status is not None and by_status.kind in _HTTP_AUTHORITATIVE:
            return ProviderError(by_status.kind, detail, retry_after_s=retry_after_s)
        text = message.lower()
        mentions_key = "api key" in text or "apikey" in text
        mentions_credits = "credit" in text
        if mentions_key and mentions_credits:
            return ProviderError(ErrorKind.AUTH, detail, tag=_KEY_OR_CREDITS)
        if mentions_key:
            return ProviderError(ErrorKind.AUTH, detail)
        if mentions_credits:
            return ProviderError(ErrorKind.LIMIT_REACHED, detail)
        return ProviderError(by_status.kind if by_status else ErrorKind.UNKNOWN, detail)

    async def _verify_email(self, req: ExecRequest, secret: str) -> ExecResult:
        try:
            params = VerifyEmailIn.model_validate(req.params)
        except ValidationError as exc:
            problems = "; ".join(str(e["msg"]) for e in exc.errors(include_input=False))
            raise ProviderError(ErrorKind.BAD_REQUEST, f"invalid verify_email params: {problems}") from None

        try:
            body = await self._get_json(
                "/v2/validate",
                params={"api_key": secret, "email": params.email, "timeout": _api_timeout(req.timeout_s)},
                timeout_s=req.timeout_s,
            )
        except ProviderError as exc:
            if exc.tag == _KEY_OR_CREDITS:
                raise await self._disambiguate(exc, secret, req.timeout_s) from None
            raise
        return self._to_result(body, params.email)

    def _to_result(self, body: Any, email: str) -> ExecResult:
        if not isinstance(body, dict) or not isinstance(body.get("status"), str):
            raise ProviderError(ErrorKind.UNKNOWN, "ZeroBounce response has no 'status' field")
        raw_status: str = body["status"]
        mapped = map_zerobounce_status(raw_status, body.get("sub_status"))
        if mapped is None:
            raise ProviderError(ErrorKind.UNKNOWN, f"unrecognised ZeroBounce status {clip(raw_status, 40)!r}")
        status, sub_status = mapped

        out = VerifyEmailOut(
            email=email,
            status=status,
            sub_status=sub_status,
            provider=self.provider_id,
            checked_at=self._now(),
        )
        definitive = is_definitive(status)
        return ExecResult(
            ok=True,
            data=out.model_dump(mode="json"),
            found=definitive,
            units_used={"credits": 1.0} if definitive else {},
        )

    async def _credits(self, secret: str, timeout_s: float) -> float:
        body = await self._get_json("/v2/getcredits", params={"api_key": secret}, timeout_s=timeout_s)
        credits = as_float(body.get("Credits", body.get("credits"))) if isinstance(body, dict) else None
        if credits is None:
            raise ProviderError(ErrorKind.UNKNOWN, "getcredits response has no 'Credits' number")
        return credits

    async def _disambiguate(self, error: ProviderError, secret: str, timeout_s: float) -> ProviderError:
        """ZeroBounce said "invalid key OR out of credits": ask the (free) balance endpoint which."""
        try:
            credits = await self._credits(secret, min(timeout_s, 10.0))
        except ProviderError:
            return error  # could not tell; stays AUTH so a person checks the key and the balance
        if credits < 0:
            return ProviderError(ErrorKind.AUTH, "invalid API key (getcredits returned -1)")
        if credits == 0:
            return ProviderError(ErrorKind.LIMIT_REACHED, "out of credits (getcredits returned 0)")
        return ProviderError(
            ErrorKind.UNKNOWN, "validate reported an invalid key or no credits, but credits remain"
        )

    async def _fetch_balance(self, secret: str, timeout_s: float) -> dict[str, float]:
        credits = await self._credits(secret, timeout_s)
        if credits < 0:
            raise ProviderError(ErrorKind.AUTH, "invalid API key (getcredits returned -1)")
        return {"credits": credits}
