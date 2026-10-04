"""Reoon Email Verifier: ``verify_email`` and the account balance.

Docs (fetched 2026-10-04):
  https://www.reoon.com/articles/api-documentation-of-reoon-email-verifier/
  https://www.reoon.com/articles/meaning-of-different-email-verification-statuses/

* ``GET https://emailverifier.reoon.com/api/v1/verify?email=..&key=..&mode=quick|power``. The key travels
  in the query string; the template keeps it out of errors and logs.
* ``mode`` comes from the connection's ``meta["mode"]`` and defaults to ``power``: quick mode never
  checks the inbox (every address on a healthy domain is "valid"), which is useless as a verdict.
  Power mode can take "a few seconds to more than a minute", so give such calls a generous
  ``timeout_s``; a client-side timeout is reported as ``TIMEOUT``.
* Charging: one credit per verdict, except ``unknown`` which Reoon refunds. So ``units_used`` is
  ``{"credits": 1}`` for every definitive status and ``{}`` for ``unknown`` (which is ``found=False``).
* Reoon documents ``{"status": "error", "reason": "..."}`` for its bulk endpoints; the single-verify
  error shape is not documented, so failures are classified from the HTTP status first and the
  ``reason`` text second (see ``_kind_from_reason``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from farm.adapters._template import ApiAdapter, Handler, ProviderError, as_float, clip
from farm.capabilities.schemas import VerifyEmailIn, VerifyEmailOut, is_definitive, map_reoon_status
from farm.executors.base import ErrorKind, ExecRequest, ExecResult

MODES = ("quick", "power")
DEFAULT_MODE = "power"

# HTTP statuses that already say what is wrong; a "reason" text must not override them.
_HTTP_AUTHORITATIVE = {
    ErrorKind.RATE_LIMITED,
    ErrorKind.AUTH,
    ErrorKind.LIMIT_REACHED,
    ErrorKind.SERVER,
    ErrorKind.TIMEOUT,
}


def _kind_from_reason(reason: str) -> ErrorKind | None:
    """Best-effort classification of Reoon's free-text ``reason`` (order matters: rate before limit)."""
    text = reason.lower()
    if any(w in text for w in ("rate limit", "too many", "thread", "concurren", "slow down")):
        return ErrorKind.RATE_LIMITED
    if any(w in text for w in ("api key", "invalid key", "key not", "unauthor", "forbidden", "inactive")):
        return ErrorKind.AUTH
    if any(w in text for w in ("credit", "balance", "quota", "limit", "exceed", "insufficient")):
        return ErrorKind.LIMIT_REACHED
    if any(w in text for w in ("invalid email", "email address", "syntax", "mode", "parameter", "missing")):
        return ErrorKind.BAD_REQUEST
    return None


def _error_reason(body: Any, status_code: int) -> str | None:
    """The provider's own error text, if the body is an error. HTTP 2xx needs ``status == "error"``."""
    if not isinstance(body, dict):
        return None
    reason = body.get("reason") or body.get("message") or body.get("error")
    is_error_body = str(body.get("status", "")).lower() == "error"
    if status_code < 300 and not is_error_body:
        return None
    if isinstance(reason, str) and reason.strip():
        return clip(reason)
    return "error" if is_error_body else None


class ReoonAdapter(ApiAdapter):
    provider_id = "reoon"
    base_url = "https://emailverifier.reoon.com"

    def capabilities(self) -> Mapping[str, Handler]:
        return {"verify_email": self._verify_email}

    def _error_from_response(
        self, status_code: int, body: Any, retry_after_s: float | None
    ) -> ProviderError | None:
        by_status = super()._error_from_response(status_code, body, retry_after_s)
        reason = _error_reason(body, status_code)
        if reason is None:
            return by_status
        detail = f"HTTP {status_code}: {reason}"
        if by_status is not None and by_status.kind in _HTTP_AUTHORITATIVE:
            return ProviderError(by_status.kind, detail, retry_after_s=retry_after_s)
        kind = _kind_from_reason(reason) or (by_status.kind if by_status else ErrorKind.UNKNOWN)
        return ProviderError(kind, detail, retry_after_s=retry_after_s)

    async def _verify_email(self, req: ExecRequest, secret: str) -> ExecResult:
        try:
            params = VerifyEmailIn.model_validate(req.params)
        except ValidationError as exc:
            problems = "; ".join(str(e["msg"]) for e in exc.errors(include_input=False))
            raise ProviderError(ErrorKind.BAD_REQUEST, f"invalid verify_email params: {problems}") from None

        mode = str(req.connection.meta.get("mode", DEFAULT_MODE)).strip().lower()
        if mode not in MODES:
            raise ProviderError(ErrorKind.BAD_REQUEST, f"connection meta.mode must be one of {MODES}")

        body = await self._get_json(
            "/api/v1/verify",
            params={"email": params.email, "key": secret, "mode": mode},
            timeout_s=req.timeout_s,
        )
        return self._to_result(body, params.email, mode)

    def _to_result(self, body: Any, email: str, requested_mode: str) -> ExecResult:
        if not isinstance(body, dict) or not isinstance(body.get("status"), str):
            raise ProviderError(ErrorKind.UNKNOWN, "Reoon response has no 'status' field")
        raw_status: str = body["status"]
        reported_mode = str(body.get("verification_mode", "")).lower()
        mode = reported_mode if reported_mode in MODES else requested_mode

        mapped = map_reoon_status(raw_status, mode)
        if mapped is None:
            raise ProviderError(ErrorKind.UNKNOWN, f"unrecognised Reoon status {clip(raw_status, 40)!r}")
        status, sub_status = mapped
        if status == "invalid" and sub_status is None:
            if body.get("is_valid_syntax") is False:
                sub_status = "failed_syntax_check"
            elif body.get("mx_accepts_mail") is False:
                sub_status = "does_not_accept_mail"

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

    async def _fetch_balance(self, secret: str, timeout_s: float) -> dict[str, float]:
        """``credits`` = daily + instant (what can still be spent); the two parts are listed separately."""
        body = await self._get_json(
            "/api/v1/check-account-balance/", params={"key": secret}, timeout_s=timeout_s
        )
        if not isinstance(body, dict):
            raise ProviderError(ErrorKind.UNKNOWN, "unexpected balance response shape")
        api_status = body.get("api_status")
        if isinstance(api_status, str) and api_status.lower() != "active":
            raise ProviderError(
                ErrorKind.AUTH, f"API key is not active (api_status {clip(api_status, 40)!r})"
            )
        daily = as_float(body.get("remaining_daily_credits"))
        instant = as_float(body.get("remaining_instant_credits"))
        if daily is None and instant is None:
            raise ProviderError(ErrorKind.UNKNOWN, "balance response has no credit fields")
        daily = daily or 0.0
        instant = instant or 0.0
        return {"credits": daily + instant, "daily_credits": daily, "instant_credits": instant}
