"""Hunter API v2: ``find_email`` (email-finder), ``verify_email`` (email-verifier) and the account balance.

Docs (fetched 2026-10-04): https://hunter.io/api-documentation/v2  (sections Authentication, Errors,
Email Finder, Email Verifier, Account Information)

* The key travels in the ``X-API-KEY`` header (the docs allow it next to ``api_key=`` and Bearer), so it is
  never part of a URL.
* Hunter's status vocabulary is unusual: **403 is the rate limit** ("You have reached the rate limit") and
  **429 is the usage limit** ("You have reached your usage limit"), the reverse of the usual reading. The
  adapter follows the docs, but an error ``id`` containing ``rate_limit`` always means a rate limit and one
  containing ``usage_limit`` or ``limit_reached`` always means exhausted credits.
* Charging: the finder costs one search only when an email is found ("If no email can be found, no credit is
  charged"); the verifier costs one verification per answer. Hunter does not say whether an ``unknown``
  verdict is refunded, so it is counted (the cautious side). Units are ``searches`` and ``verifications``;
  ``balance()`` reports those and, for teams on a unified credit bucket, ``credits``.
* The verifier runs for up to 20 s and then answers HTTP 202 ("still in progress", counted once however
  often it is polled); that is reported as ``TIMEOUT``. HTTP 222 means the remote SMTP server answered
  unexpectedly and the docs say retry later: ``SERVER``. HTTP 451 (``claimed_email``: the owner asked not to
  be processed) is an empty result. Give verification calls a ``timeout_s`` of 25 or more.
* Hunter's special key ``test-api-key`` returns a fixed dummy answer; the tests do not use it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from farm.adapters._template import (
    ApiAdapter,
    Handler,
    ProviderError,
    as_dict,
    as_float,
    as_list,
    as_text,
    clip,
    empty_result,
    parse_params,
)
from farm.capabilities.schemas import (
    FindEmailIn,
    FindEmailOut,
    VerifyEmailIn,
    VerifyEmailOut,
    VerifyStatus,
    is_definitive,
    map_hunter_status,
)
from farm.executors.base import ErrorKind, ExecRequest, ExecResult

_FINDER_STATUS: dict[str, VerifyStatus] = {"valid": "valid", "accept_all": "catch_all"}
_HTTP_AUTHORITATIVE = {ErrorKind.SERVER, ErrorKind.TIMEOUT}


def _first_error(body: Any) -> tuple[str, str | None]:
    """(error ``id``, ``details``) of the first entry of Hunter's ``{"errors": [...]}`` body."""
    errors = as_list(as_dict(body).get("errors"))
    first = as_dict(errors[0]) if errors else {}
    return (as_text(first.get("id")) or "").lower(), as_text(first.get("details"))


class HunterAdapter(ApiAdapter):
    provider_id = "hunter"
    base_url = "https://api.hunter.io/v2"
    balance_unit = "searches"

    def capabilities(self) -> Mapping[str, Handler]:
        return {"find_email": self._find_email, "verify_email": self._verify_email}

    @staticmethod
    def _headers(secret: str) -> dict[str, str]:
        return {"X-API-KEY": secret}

    def _error_from_response(
        self, status_code: int, body: Any, retry_after_s: float | None
    ) -> ProviderError | None:
        if status_code == 202:
            return ProviderError(
                ErrorKind.TIMEOUT,
                "HTTP 202: verification still in progress; ask again later",
                retry_after_s=retry_after_s,
            )
        if status_code == 222:
            return ProviderError(
                ErrorKind.SERVER, "HTTP 222: unexpected answer from the remote SMTP server; retry later"
            )
        error_id, details = _first_error(body)
        if status_code == 403:
            kind = ErrorKind.RATE_LIMITED
        elif status_code == 429:
            kind = ErrorKind.LIMIT_REACHED
        elif status_code == 451:
            kind = ErrorKind.EMPTY
        else:
            by_status = super()._error_from_response(status_code, body, retry_after_s)
            if by_status is None:
                return None
            kind = by_status.kind
        if kind not in _HTTP_AUTHORITATIVE:
            if "rate_limit" in error_id:
                kind = ErrorKind.RATE_LIMITED
            elif "usage_limit" in error_id or "limit_reached" in error_id:
                kind = ErrorKind.LIMIT_REACHED
        detail = f"HTTP {status_code}" + (f": {clip(details)}" if details else "")
        if error_id:
            detail += f" [{clip(error_id, 60)}]"
        return ProviderError(kind, detail, retry_after_s=retry_after_s)

    # -- find_email -------------------------------------------------------------------------------

    async def _find_email(self, req: ExecRequest, secret: str) -> ExecResult:
        params = parse_params(FindEmailIn, req.params, "find_email")
        base: dict[str, Any] = {
            "source": self._source(req),
            "observed_at": self._now(),
            "first_name": params.first_name,
            "last_name": params.last_name,
            "domain": params.domain,
        }
        free = {"searches": 0.0}
        try:
            body = await self._get_json(
                "/email-finder",
                params={
                    "domain": params.domain,
                    "first_name": params.first_name,
                    "last_name": params.last_name,
                },
                headers=self._headers(secret),
                timeout_s=req.timeout_s,
            )
        except ProviderError as exc:
            if exc.kind is ErrorKind.EMPTY:
                return empty_result(FindEmailOut(**base), str(exc), units_used=free)
            raise
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ProviderError(ErrorKind.UNKNOWN, "Hunter email-finder response has no 'data' object")
        email = as_text(data.get("email"))
        if email is None or "@" not in email:
            reason = f"Hunter found no email for {params.first_name} {params.last_name} at {params.domain}"
            return empty_result(FindEmailOut(**base), reason, units_used=free)
        verification = as_dict(data.get("verification"))
        raw_status = as_text(verification.get("status"))
        score = as_float(data.get("score"))
        out = FindEmailOut(
            **base,
            email=email,
            confidence=None if score is None else max(0, min(100, round(score))),
            verification=_FINDER_STATUS.get((raw_status or "").lower(), "unknown"),
            verification_detail=raw_status,
            position=as_text(data.get("position")),
            linkedin=as_text(data.get("linkedin_url")),
        )
        return ExecResult(ok=True, data=out.model_dump(mode="json"), found=True, units_used={"searches": 1.0})

    # -- verify_email -----------------------------------------------------------------------------

    async def _verify_email(self, req: ExecRequest, secret: str) -> ExecResult:
        params = parse_params(VerifyEmailIn, req.params, "verify_email")
        body = await self._get_json(
            "/email-verifier",
            params={"email": params.email},
            headers=self._headers(secret),
            timeout_s=req.timeout_s,
        )
        data = as_dict(as_dict(body).get("data")) if isinstance(body, dict) else {}
        raw_status = as_text(data.get("status"))
        if raw_status is None:
            raise ProviderError(ErrorKind.UNKNOWN, "Hunter email-verifier response has no 'status' field")
        mapped = map_hunter_status(raw_status)
        if mapped is None:
            raise ProviderError(ErrorKind.UNKNOWN, f"unrecognised Hunter status {clip(raw_status, 40)!r}")
        status, sub_status = mapped
        if status == "invalid" and sub_status is None:
            if data.get("regexp") is False:
                sub_status = "failed_syntax_check"
            elif data.get("mx_records") is False:
                sub_status = "does_not_accept_mail"
        out = VerifyEmailOut(
            email=params.email,
            status=status,
            sub_status=sub_status,
            provider=self.provider_id,
            checked_at=self._now(),
        )
        return ExecResult(
            ok=True,
            data=out.model_dump(mode="json"),
            found=is_definitive(status),
            units_used={"verifications": 1.0},
        )

    # -- balance ----------------------------------------------------------------------------------

    async def _fetch_balance(self, secret: str, timeout_s: float) -> dict[str, float]:
        """Remaining ``searches`` and ``verifications`` (and ``credits`` on a unified-credit team)."""
        body = await self._get_json("/account", params={}, headers=self._headers(secret), timeout_s=timeout_s)
        requests = as_dict(as_dict(as_dict(body).get("data")).get("requests"))
        units: dict[str, float] = {}
        for unit in ("credits", "searches", "verifications"):
            remaining = as_float(as_dict(requests.get(unit)).get("remaining"))
            if remaining is not None:
                units[unit] = remaining
        if not units:
            raise ProviderError(ErrorKind.UNKNOWN, "Hunter account response has no remaining-request fields")
        return units
