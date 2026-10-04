"""Apollo.io REST API: ``find_person`` (people search), ``enrich_company`` and ``find_email`` (people match).

Docs (fetched 2026-10-04; every page also exists as markdown by appending ``.md``):
  https://docs.apollo.io/reference/authentication
  https://docs.apollo.io/reference/people-api-search       POST /mixed_people/api_search
  https://docs.apollo.io/reference/organization-enrichment GET  /organizations/enrich
  https://docs.apollo.io/reference/people-enrichment       POST /people/match
  https://docs.apollo.io/docs/api-pricing                  credit rules
  https://docs.apollo.io/reference/rate-limits             limits + ``retry-after``
  https://docs.apollo.io/reference/status-codes            ``error_details.code``

* The key travels in the ``x-api-key`` header, never in the URL. All parameters are query parameters
  (POST included; the request has no body).
* The brief named ``mixed_people/search``; Apollo's current docs call the free endpoint
  ``mixed_people/api_search`` (0 credits) and say it returns no emails, no phone numbers and no LinkedIn URL,
  and masks the surname (``last_name_obfuscated``). ``find_person`` therefore marks such names
  ``name_is_partial``; a full name and the profile need ``people/match`` (1 credit).
* Charging (``units_used["credits"]``): people search 0; organization enrichment 1 when an organization comes
  back; people match 1 when Apollo identifies a person (1 credit covers demographics *or* email; no mobile or
  waterfall is ever requested, so the 8-credit mobile surcharge cannot apply). Apollo charges nothing when
  ``match_confidence`` is ``none``. Whether organization enrichment bills a non-match is not documented, so
  no match is counted as 0.
* Failures: Apollo is moving every error to ``error_details.code`` (``DOMAIN.CATEGORY.REASON``). Status first,
  then that code, then the words "credit" in the text, decide the kind. A 429 carries ``retry-after`` (seconds
  until the exhausted window reopens). A 401 body is plain text. The out-of-credits status is not
  documented, so it is recognised by wording (and 402).
* No balance endpoint is used: the credit-usage endpoint needs a master key and its response shape is not
  documented well enough to rely on.
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
    as_int,
    as_list,
    as_text,
    clip,
    empty_result,
    parse_params,
)
from farm.capabilities.schemas import (
    CompanyLocation,
    EnrichCompanyIn,
    EnrichCompanyOut,
    FindEmailIn,
    FindEmailOut,
    FindPersonIn,
    FindPersonOut,
    PersonHit,
    Socials,
    VerifyStatus,
    employee_size_range,
)
from farm.executors.base import ErrorKind, ExecRequest, ExecResult

# Statuses that already say what is wrong; Apollo's code or wording must not override them.
_HTTP_AUTHORITATIVE = {ErrorKind.RATE_LIMITED, ErrorKind.SERVER, ErrorKind.TIMEOUT}
_MAX_TECH_HINTS = 50


def _error_texts(body: Any) -> tuple[str, str | None, float | None]:
    """(``error_details.code`` or "", the provider's own message, ``retry_after_seconds`` hint)."""
    if not isinstance(body, dict):
        return "", None, None
    details = as_dict(body.get("error_details"))
    code = as_text(details.get("code")) or ""
    message = as_text(details.get("message")) or as_text(body.get("error")) or as_text(body.get("message"))
    hint = next(
        (
            seconds
            for suggestion in as_list(details.get("suggestions"))
            if (seconds := as_float(as_dict(suggestion).get("retry_after_seconds"))) is not None
        ),
        None,
    )
    return code, message, hint


class ApolloAdapter(ApiAdapter):
    provider_id = "apollo"
    base_url = "https://api.apollo.io/api/v1"

    def capabilities(self) -> Mapping[str, Handler]:
        return {
            "find_person": self._find_person,
            "enrich_company": self._enrich_company,
            "find_email": self._find_email,
        }

    @staticmethod
    def _headers(secret: str) -> dict[str, str]:
        return {"x-api-key": secret, "Cache-Control": "no-cache"}

    def _error_from_response(
        self, status_code: int, body: Any, retry_after_s: float | None
    ) -> ProviderError | None:
        code, message, hint = _error_texts(body)
        retry_after_s = retry_after_s if retry_after_s is not None else hint
        by_status = super()._error_from_response(status_code, body, retry_after_s)
        if by_status is None:
            return None
        kind = by_status.kind
        if kind not in _HTTP_AUTHORITATIVE:
            if "RATE_LIMIT" in code:
                kind = ErrorKind.RATE_LIMITED
            elif "CREDIT" in code or "credit" in (message or "").lower():
                kind = ErrorKind.LIMIT_REACHED
            elif code.startswith("AUTH."):
                kind = ErrorKind.AUTH
            elif ".VALIDATION." in code:
                kind = ErrorKind.BAD_REQUEST
        detail = f"HTTP {status_code}" + (f": {clip(message)}" if message else "")
        if code:
            detail += f" [{clip(code, 80)}]"
        return ProviderError(kind, detail, retry_after_s=retry_after_s)

    # -- find_person ------------------------------------------------------------------------------

    async def _find_person(self, req: ExecRequest, secret: str) -> ExecResult:
        params = parse_params(FindPersonIn, req.params, "find_person")
        if params.company_domain is None:
            raise ProviderError(
                ErrorKind.BAD_REQUEST,
                "Apollo people search filters by company domain; company_name alone is not supported",
            )
        query: dict[str, str | int | float | bool | list[str]] = {
            "q_organization_domains_list[]": [params.company_domain],
            "page": 1,
            "per_page": params.limit,
        }
        if params.titles:
            query["person_titles[]"] = list(params.titles)
        if params.seniority:
            query["person_seniorities[]"] = list(params.seniority)

        body = await self._request_json(
            "POST",
            "/mixed_people/api_search",
            params=query,
            headers=self._headers(secret),
            timeout_s=req.timeout_s,
        )
        if not isinstance(body, dict) or not isinstance(body.get("people"), list):
            raise ProviderError(ErrorKind.UNKNOWN, "Apollo people search response has no 'people' list")
        people = [hit for item in body["people"] if (hit := self._person_hit(item)) is not None]
        out = FindPersonOut(
            source=self._source(req),
            observed_at=self._now(),
            people=people,
            total=as_int(body.get("total_entries")),
        )
        free = {"credits": 0.0}  # people search is documented as 0 credits
        if not people:
            reason = f"Apollo knows no matching people at {params.company_domain}"
            return empty_result(out, reason, units_used=free)
        return ExecResult(ok=True, data=out.model_dump(mode="json"), found=True, units_used=free)

    @staticmethod
    def _person_hit(item: Any) -> PersonHit | None:
        if not isinstance(item, dict):
            raise ProviderError(ErrorKind.UNKNOWN, "Apollo people search returned a non-object person")
        first = as_text(item.get("first_name"))
        full_last = as_text(item.get("last_name"))
        masked_last = as_text(item.get("last_name_obfuscated"))
        last = full_last or masked_last
        full_name = as_text(item.get("name"))
        name = full_name or " ".join(part for part in (first, last) if part)
        if not name:
            return None
        has_email = item.get("has_email")
        return PersonHit(
            provider_person_id=as_text(item.get("id")),
            name=name,
            first_name=first,
            last_name=last,
            name_is_partial=full_last is None and full_name is None and masked_last is not None,
            title=as_text(item.get("title")),
            company=as_text(as_dict(item.get("organization")).get("name")),
            linkedin=as_text(item.get("linkedin_url")),
            has_email=has_email if isinstance(has_email, bool) else None,
        )

    # -- enrich_company ---------------------------------------------------------------------------

    async def _enrich_company(self, req: ExecRequest, secret: str) -> ExecResult:
        params = parse_params(EnrichCompanyIn, req.params, "enrich_company")
        query: dict[str, str | int | float | bool | list[str]] = {"domain": params.domain}
        if params.name:
            query["name"] = params.name
        body = await self._request_json(
            "GET",
            "/organizations/enrich",
            params=query,
            headers=self._headers(secret),
            timeout_s=req.timeout_s,
        )
        if not isinstance(body, dict):
            raise ProviderError(ErrorKind.UNKNOWN, "Apollo organization enrichment response is not an object")
        org = as_dict(body.get("organization"))
        source, observed_at = self._source(req), self._now()
        if not org:
            return empty_result(
                EnrichCompanyOut(source=source, observed_at=observed_at, domain=params.domain),
                f"Apollo has no organization for {params.domain}",
                units_used={"credits": 0.0},
            )
        employees = as_int(org.get("estimated_num_employees"))
        tech = [t for t in (as_text(x) for x in as_list(org.get("technology_names"))) if t]
        if not tech:
            current = as_list(org.get("current_technologies"))
            tech = [t for t in (as_text(as_dict(x).get("name")) for x in current) if t]
        out = EnrichCompanyOut(
            source=source,
            observed_at=observed_at,
            domain=as_text(org.get("primary_domain")) or params.domain,
            name=as_text(org.get("name")),
            industry=as_text(org.get("industry")),
            employee_count=employees,
            size_range=employee_size_range(employees),
            location=CompanyLocation(
                city=as_text(org.get("city")),
                region=as_text(org.get("state")),
                country=as_text(org.get("country")),
            ),
            socials=Socials(
                linkedin=as_text(org.get("linkedin_url")),
                twitter=as_text(org.get("twitter_url")),
                facebook=as_text(org.get("facebook_url")),
            ),
            tech_hints=tech[:_MAX_TECH_HINTS],
            description=as_text(org.get("short_description")),
            founded_year=as_int(org.get("founded_year")),
            website=as_text(org.get("website_url")),
        )
        return ExecResult(ok=True, data=out.model_dump(mode="json"), found=True, units_used={"credits": 1.0})

    # -- find_email -------------------------------------------------------------------------------

    async def _find_email(self, req: ExecRequest, secret: str) -> ExecResult:
        params = parse_params(FindEmailIn, req.params, "find_email")
        body = await self._request_json(
            "POST",
            "/people/match",
            params={"first_name": params.first_name, "last_name": params.last_name, "domain": params.domain},
            headers=self._headers(secret),
            timeout_s=req.timeout_s,
        )
        if not isinstance(body, dict):
            raise ProviderError(ErrorKind.UNKNOWN, "Apollo people match response is not an object")
        person = as_dict(body.get("person"))
        confidence = as_text(person.get("match_confidence"))
        identified = bool(as_text(person.get("id")) or as_text(person.get("email")))
        matched = identified and confidence != "none"
        base: dict[str, Any] = {
            "source": self._source(req),
            "observed_at": self._now(),
            "first_name": params.first_name,
            "last_name": params.last_name,
            "domain": params.domain,
        }
        if not matched:
            return empty_result(
                FindEmailOut(**base),
                f"Apollo could not match {params.first_name} {params.last_name} at {params.domain}",
                units_used={"credits": 0.0},
            )
        email = _usable_email(person.get("email"))
        status = as_text(person.get("email_status"))
        extrapolated = as_float(person.get("extrapolated_email_confidence"))
        out = FindEmailOut(
            **base,
            email=email,
            confidence=None if extrapolated is None else max(0, min(100, round(extrapolated * 100))),
            verification=None if email is None else _verification(status),
            verification_detail=status if email else None,
            position=as_text(person.get("title")),
            linkedin=as_text(person.get("linkedin_url")),
        )
        charged = {"credits": 1.0}  # a match is billed for its demographics even when no email is revealed
        if email is None:
            reason = "Apollo matched the person but holds no email address"
            return empty_result(out, reason, units_used=charged)
        return ExecResult(ok=True, data=out.model_dump(mode="json"), found=True, units_used=charged)


def _usable_email(value: object) -> str | None:
    """Apollo masks locked addresses with a placeholder (``email_not_unlocked@...``); that is not an email."""
    text = as_text(value)
    if text is None or "@" not in text or text.lower().startswith("email_not_unlocked"):
        return None
    return text


def _verification(email_status: str | None) -> VerifyStatus:
    return "valid" if (email_status or "").lower() == "verified" else "unknown"
