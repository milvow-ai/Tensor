"""Adzuna jobs API: ``jobs_lookup`` by keywords and place (``/jobs/{country}/search/{page}``).

Docs (fetched 2026-10-04):
  https://developer.adzuna.com/overview          root URL, ``app_id``/``app_key``, JSON via the Accept header
  https://developer.adzuna.com/docs/search       parameters and the sample response
  https://developer.adzuna.com/docs/terms_of_service   limits: 25/minute, 250/day, 1000/week, 2500/month

* Adzuna wants two credentials in the query string. The key is the connection's ``auth_ref`` (e.g.
  ``env:ADZUNA_APP_KEY``) and the id is a second reference in the connection's ``meta["app_id_ref"]``
  (e.g. ``env:ADZUNA_APP_ID``). Both are resolved at call time and registered for redaction, so neither value
  ever sits in the registry or in a log. A missing or unresolvable reference is an AUTH failure.
* The default format without an ``Accept: application/json`` header is JSONP; the template sends the header.
* Input: ``what`` and/or ``where`` (+ ``country``, default ``us``). ``company`` alone searches the name as
  keywords and keeps only postings whose company name contains it. Adzuna cannot look up a job-board token,
  so ``board`` without any other target is a BAD_REQUEST here.
* Salaries Adzuna *predicts* (``salary_is_predicted`` = 1) are not the employer's figures and are dropped.
* Charging: one ``requests`` unit per call. Only the daily cap (250) is modelled in the ledger; the weekly and
  monthly caps are enforced by Adzuna itself and surface as a 429.
* The error body shape (``exception``/``display``/``description``) is not in the pages above (the interactive
  endpoint docs need a login) and is UNVERIFIED; classification therefore leans on the HTTP status first.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from farm.adapters._template import (
    ApiAdapter,
    Handler,
    ProviderError,
    as_dict,
    as_float,
    as_int,
    as_list,
    as_text,
    clip,
    empty_result,
    parse_params,
)
from farm.capabilities.schemas import JobPosting, JobsLookupIn, JobsLookupOut
from farm.executors.base import ErrorKind, ExecRequest, ExecResult
from farm.secrets import AuthRefError, resolve_auth

_HTTP_AUTHORITATIVE = {ErrorKind.SERVER, ErrorKind.TIMEOUT}
_LONG_WINDOWS = ("per day", "daily", "per week", "weekly", "per month", "monthly")


def _error_facts(body: Any) -> tuple[str, str]:
    """(``exception`` code upper-cased, human text) of an Adzuna error body."""
    data = as_dict(body)
    code = (as_text(data.get("exception")) or "").upper()
    text = " ".join(t for t in (as_text(data.get("display")), as_text(data.get("description"))) if t)
    return code, text


def _parse_time(value: object) -> datetime | None:
    text = as_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _posting(item: Any) -> JobPosting | None:
    data = as_dict(item)
    job_id = data.get("id")
    title = as_text(data.get("title"))
    url = as_text(data.get("redirect_url"))
    if job_id in (None, "") or not title or not url:
        return None  # a listing that cannot be identified or opened is of no use to a caller
    predicted = str(data.get("salary_is_predicted", "0")).strip().lower() in ("1", "true")
    return JobPosting(
        id=str(job_id),
        title=title,
        company=as_text(as_dict(data.get("company")).get("display_name")),
        location=as_text(as_dict(data.get("location")).get("display_name")),
        url=url,
        posted_at=_parse_time(data.get("created")),
        department=as_text(as_dict(data.get("category")).get("label")),
        employment_type=as_text(data.get("contract_time")) or as_text(data.get("contract_type")),
        salary_min=None if predicted else as_float(data.get("salary_min")),
        salary_max=None if predicted else as_float(data.get("salary_max")),
        platform="adzuna",
    )


class AdzunaAdapter(ApiAdapter):
    provider_id = "adzuna"
    base_url = "https://api.adzuna.com/v1/api"

    def capabilities(self) -> Mapping[str, Handler]:
        return {"jobs_lookup": self._jobs_lookup}

    def _error_from_response(
        self, status_code: int, body: Any, retry_after_s: float | None
    ) -> ProviderError | None:
        by_status = super()._error_from_response(status_code, body, retry_after_s)
        if by_status is None:
            return None
        code, text = _error_facts(body)
        kind = by_status.kind
        if kind not in _HTTP_AUTHORITATIVE:
            lowered = text.lower()
            if "AUTH" in code:
                kind = ErrorKind.AUTH
            elif status_code == 429 and any(window in lowered for window in _LONG_WINDOWS):
                kind = ErrorKind.LIMIT_REACHED
        detail = f"HTTP {status_code}" + (f": {clip(text)}" if text else "")
        if code:
            detail += f" [{clip(code, 60)}]"
        return ProviderError(kind, detail, retry_after_s=retry_after_s)

    @staticmethod
    def _app_id(req: ExecRequest) -> str:
        ref = as_text(req.connection.meta.get("app_id_ref"))
        if ref is None:
            raise ProviderError(
                ErrorKind.AUTH,
                "connection meta.app_id_ref must reference the Adzuna app id, e.g. 'env:ADZUNA_APP_ID'",
            )
        try:
            return resolve_auth(ref)
        except AuthRefError as exc:
            raise ProviderError(ErrorKind.AUTH, f"app id: {exc}") from None

    async def _jobs_lookup(self, req: ExecRequest, secret: str) -> ExecResult:
        params = parse_params(JobsLookupIn, req.params, "jobs_lookup")
        keywords = params.what or params.company
        if keywords is None and params.where is None:
            raise ProviderError(
                ErrorKind.BAD_REQUEST,
                "Adzuna searches by what/where (or a company name); it cannot read a job board",
            )
        query: dict[str, str | int | float | bool] = {
            "app_id": self._app_id(req),
            "app_key": secret,
            "results_per_page": params.limit,
        }
        if keywords:
            query["what"] = keywords
        if params.where:
            query["where"] = params.where
        body = await self._get_json(f"/jobs/{params.country}/search/1", params=query, timeout_s=req.timeout_s)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ProviderError(ErrorKind.UNKNOWN, "Adzuna response has no 'results' list")

        postings = [p for item in as_list(body["results"]) if (p := _posting(item)) is not None]
        if params.company and not params.what:
            wanted = params.company.casefold()
            postings = [p for p in postings if wanted in (p.company or "").casefold()]
        out = JobsLookupOut(
            source=self._source(req),
            observed_at=self._now(),
            postings=postings[: params.limit],
            total=as_int(body.get("count")),
            platform="adzuna",
        )
        charged = {"requests": 1.0}
        if not postings:
            return empty_result(out, "Adzuna returned no postings for this search", units_used=charged)
        return ExecResult(ok=True, data=out.model_dump(mode="json"), found=True, units_used=charged)
