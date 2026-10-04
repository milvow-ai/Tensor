"""Public job-board JSON of Greenhouse, Lever and Ashby: ``jobs_lookup`` by board token or company name.

Docs (fetched 2026-10-04):
  Greenhouse  https://developers.greenhouse.io/job-board.html
              ``GET boards-api.greenhouse.io/v1/boards/{token}/jobs`` ("Job Board data is publicly available,
              so authentication is not required for any GET endpoints")
  Lever       https://github.com/lever/postings-api  ``GET api.lever.co/v0/postings/{site}?mode=json`` (EU
              accounts live on ``api.eu.lever.co`` and are not covered)
  Ashby       https://developers.ashbyhq.com/docs/public-job-posting-api
              ``GET api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true``

* No key, no quota. The connection's ``auth_ref`` is a schema placeholder and is never resolved.
* The board token is the slug in the company's careers URL (``boards.greenhouse.io/<token>``,
  ``jobs.lever.co/<site>``, ``jobs.ashbyhq.com/<name>``). ``board`` is used as given. With only ``company``,
  the adapter derives up to two candidate slugs (``Big Data`` -> ``bigdata``, ``big-data``); the result then
  says ``board_guessed=True`` because a slug match does not prove it is the same company.
* Each candidate is tried on Greenhouse, then Lever, then Ashby (or only on ``ats`` when given). The first
  platform whose board answers with a valid list wins, even when that list is empty (the company runs its
  hiring there and has no openings). A 404 means "no such board on this platform" and the next one is tried.
* An outage must not read as "the company has no board": if no platform answered and at least one failed
  with something other than a 404 (429, 5xx, timeout, malformed JSON), that failure is raised and the empty
  result is only returned when every probe was a clean 404.
* Ashby postings flagged ``isListed: false`` are reachable by direct link only and are left out. Ashby's
  documented fields have no posting id, so the last segment of ``jobUrl`` is used.
* Salaries are kept only when the platform labels them annual (Ashby ``1 YEAR`` salary components, a Lever
  ``salaryRange`` whose interval mentions a year); hourly or monthly figures would be misleading. Lever's
  ``createdAt`` (milliseconds since the epoch) is read when present; it is not in the README's field table
  and is UNVERIFIED.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from farm.adapters._template import (
    ApiAdapter,
    Handler,
    ProviderError,
    as_dict,
    as_float,
    as_int,
    as_list,
    as_text,
    empty_result,
    parse_params,
)
from farm.capabilities.schemas import AtsName, JobPosting, JobsLookupIn, JobsLookupOut
from farm.executors.base import ErrorKind, ExecRequest, ExecResult

_HOSTS: dict[AtsName, str] = {
    "greenhouse": "https://boards-api.greenhouse.io",
    "lever": "https://api.lever.co",
    "ashby": "https://api.ashbyhq.com",
}
_ORDER: tuple[AtsName, ...] = ("greenhouse", "lever", "ashby")
_COMPANY_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company"}
_COMPANY_SUFFIXES |= {"gmbh", "plc", "ag"}
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")


def slug_candidates(company: str) -> list[str]:
    """Plausible board tokens for a company name: ``Acme Corp.`` -> ``["acme"]``, ``Big Data`` -> two."""
    folded = unicodedata.normalize("NFKD", company).encode("ascii", "ignore").decode("ascii").casefold()
    words = re.findall(r"[a-z0-9]+", folded)
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    candidates = ["".join(words), "-".join(words)]
    unique: list[str] = []
    for candidate in candidates:
        if _TOKEN.fullmatch(candidate) and candidate not in unique:
            unique.append(candidate)
    return unique


def _time_from_ms(value: object) -> datetime | None:
    millis = as_float(value)
    if millis is None or millis <= 0:
        return None
    try:
        return datetime.fromtimestamp(millis / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _time_from_iso(value: object) -> datetime | None:
    text = as_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _greenhouse(body: Any) -> tuple[list[JobPosting], int | None]:
    if not isinstance(body, dict) or not isinstance(body.get("jobs"), list):
        raise ProviderError(ErrorKind.UNKNOWN, "Greenhouse board response has no 'jobs' list")
    postings: list[JobPosting] = []
    for item in as_list(body["jobs"]):
        data = as_dict(item)
        job_id, title, url = data.get("id"), as_text(data.get("title")), as_text(data.get("absolute_url"))
        if job_id in (None, "") or not title or not url:
            continue
        postings.append(
            JobPosting(
                id=str(job_id),
                title=title,
                location=as_text(as_dict(data.get("location")).get("name")),
                url=url,
                platform="greenhouse",
            )
        )
    return postings, as_int(as_dict(body.get("meta")).get("total"))


def _lever(body: Any) -> tuple[list[JobPosting], int | None]:
    if not isinstance(body, list):
        raise ProviderError(ErrorKind.UNKNOWN, "Lever postings response is not a list")
    postings: list[JobPosting] = []
    for item in body:
        data = as_dict(item)
        job_id, title, url = (
            as_text(data.get("id")),
            as_text(data.get("text")),
            as_text(data.get("hostedUrl")),
        )
        if not job_id or not title or not url:
            continue
        categories = as_dict(data.get("categories"))
        workplace = (as_text(data.get("workplaceType")) or "").lower()
        salary = as_dict(data.get("salaryRange"))
        annual = "year" in (as_text(salary.get("interval")) or "").lower()
        postings.append(
            JobPosting(
                id=job_id,
                title=title,
                location=as_text(categories.get("location")),
                url=url,
                posted_at=_time_from_ms(data.get("createdAt")),
                department=as_text(categories.get("department")) or as_text(categories.get("team")),
                employment_type=as_text(categories.get("commitment")),
                remote=None if workplace in ("", "unspecified") else workplace == "remote",
                salary_min=as_float(salary.get("min")) if annual else None,
                salary_max=as_float(salary.get("max")) if annual else None,
                platform="lever",
            )
        )
    return postings, len(postings)


def _ashby(body: Any) -> tuple[list[JobPosting], int | None]:
    if not isinstance(body, dict) or not isinstance(body.get("jobs"), list):
        raise ProviderError(ErrorKind.UNKNOWN, "Ashby board response has no 'jobs' list")
    postings: list[JobPosting] = []
    for item in as_list(body["jobs"]):
        data = as_dict(item)
        title, url = as_text(data.get("title")), as_text(data.get("jobUrl"))
        if not title or not url or data.get("isListed") is False:
            continue
        job_id = as_text(data.get("id")) or url.rstrip("/").rsplit("/", 1)[-1]
        remote = data.get("isRemote")
        salary_min = salary_max = None
        for component in as_list(as_dict(data.get("compensation")).get("summaryComponents")):
            part = as_dict(component)
            if part.get("compensationType") == "Salary" and "YEAR" in str(part.get("interval", "")).upper():
                salary_min, salary_max = as_float(part.get("minValue")), as_float(part.get("maxValue"))
                break
        postings.append(
            JobPosting(
                id=job_id,
                title=title,
                location=as_text(data.get("location")),
                url=url,
                posted_at=_time_from_iso(data.get("publishedAt")),
                department=as_text(data.get("department")) or as_text(data.get("team")),
                employment_type=as_text(data.get("employmentType")),
                remote=remote if isinstance(remote, bool) else None,
                salary_min=salary_min,
                salary_max=salary_max,
                platform="ashby",
            )
        )
    return postings, len(postings)


_PARSERS: dict[AtsName, Callable[[Any], tuple[list[JobPosting], int | None]]] = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
}


def _endpoint(ats: AtsName, token: str) -> tuple[str, dict[str, str]]:
    safe = quote(token, safe="")
    if ats == "greenhouse":
        return f"/v1/boards/{safe}/jobs", {}
    if ats == "lever":
        return f"/v0/postings/{safe}", {"mode": "json"}
    return f"/posting-api/job-board/{safe}", {"includeCompensation": "true"}


class AtsPublicAdapter(ApiAdapter):
    provider_id = "ats_public"
    base_url = _HOSTS["greenhouse"]
    requires_auth = False

    def capabilities(self) -> Mapping[str, Handler]:
        return {"jobs_lookup": self._jobs_lookup}

    async def _jobs_lookup(self, req: ExecRequest, secret: str) -> ExecResult:
        params = parse_params(JobsLookupIn, req.params, "jobs_lookup")
        if params.board:
            candidates, guessed = [params.board], False
        elif params.company:
            candidates, guessed = slug_candidates(params.company), True
        else:
            raise ProviderError(
                ErrorKind.BAD_REQUEST, "job boards are looked up by board or company; what/where need Adzuna"
            )
        if not candidates:
            raise ProviderError(ErrorKind.BAD_REQUEST, "company name has no usable letters or digits")
        platforms: tuple[AtsName, ...] = (params.ats,) if params.ats else _ORDER

        deadline = time.monotonic() + req.timeout_s
        failure: ProviderError | None = None
        calls = 0
        for token in candidates:
            for ats in platforms:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise failure or ProviderError(ErrorKind.TIMEOUT, f"no answer within {req.timeout_s:g}s")
                path, query = _endpoint(ats, token)
                calls += 1
                try:
                    body = await self._get_json(path, params=query, timeout_s=remaining, base_url=_HOSTS[ats])
                    postings, total = _PARSERS[ats](body)
                except ProviderError as exc:
                    if exc.kind is not ErrorKind.EMPTY:  # a 404 only says "no such board here"
                        failure = failure or exc
                    continue
                out = JobsLookupOut(
                    source=self._source(req),
                    observed_at=self._now(),
                    postings=postings[: params.limit],
                    total=total,
                    platform=ats,
                    board=token,
                    board_guessed=guessed,
                )
                if not postings:
                    return empty_result(
                        out,
                        f"{ats} board '{token}' has no open postings",
                        units_used={"requests": float(calls)},
                    )
                return ExecResult(
                    ok=True,
                    data=out.model_dump(mode="json"),
                    found=True,
                    units_used={"requests": float(calls)},
                )
        if failure is not None:
            raise failure
        tried = ", ".join(candidates)
        return empty_result(
            JobsLookupOut(
                source=self._source(req), observed_at=self._now(), postings=[], board_guessed=guessed
            ),
            f"no public Greenhouse, Lever or Ashby board named {tried}",
            units_used={"requests": float(calls)},
        )
