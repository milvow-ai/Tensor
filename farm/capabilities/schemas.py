"""Normalised input/output models per capability, plus provider-status -> normalised-status mapping.

Every provider reports email verdicts in its own vocabulary. The Farm exposes one:

    valid       the mailbox exists and accepts mail
    invalid     the mailbox / domain does not exist (hard bounce)
    risky       deliverable or unprovable, but not safe to send blindly: disposable, role account
                (info@, sales@), inbox full, spam trap, abuse/complainer, "do not mail", or a mailbox
                that was never actually checked
    catch_all   the domain accepts every address, so the mailbox cannot be confirmed
    unknown     no verdict (greylisting, timeout, anti-spam system); providers refund these

The mapping is deliberately provider-independent: the same address must normalise to the same status
whichever pool answered, otherwise a fallback from one provider to the next would change the meaning.

The other capabilities (find_person, find_email, enrich_company, pagespeed, jobs_lookup, extract, classify)
follow the same rule: one input model and one output model per capability, registered in
``CAPABILITY_MODELS``. Every output after ``verify_email`` is a :class:`Sourced` model, so it always says
which provider pool and connection produced it and when.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

# jsonschema ships no py.typed marker, so mypy cannot see its types; it is only used through the two typed
# wrappers below (check_json_schema, json_schema_errors). It is already installed as a dependency of mcp.
import jsonschema  # type: ignore[import-untyped]
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

VerifyStatus = Literal["valid", "invalid", "risky", "catch_all", "unknown"]
VERIFY_STATUSES: tuple[str, ...] = ("valid", "invalid", "risky", "catch_all", "unknown")

# A mapped provider verdict: (normalised status, sub_status or None).
Mapped = tuple[VerifyStatus, str | None]

_LOCAL_PART = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+$")
_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalise_email(value: str) -> str:
    """Strip, validate (pragmatic RFC 5321 subset) and canonicalise an address.

    Only the domain is lower-cased (local parts are case-sensitive in theory); an internationalised
    domain becomes punycode. Raises ``ValueError`` with a short reason (pydantic turns it into a
    validation error). ``email-validator`` is not a declared dependency, hence no ``EmailStr``.
    """
    email = value.strip()
    if email.count("@") != 1:
        raise ValueError("email must contain exactly one '@'")
    local, domain = email.split("@")
    if not 1 <= len(local) <= 64:
        raise ValueError("email local part must be 1-64 characters")
    if not _LOCAL_PART.fullmatch(local) or local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValueError("email local part has invalid characters or dots")
    return f"{local}@{_hostname(domain, 'email domain')}"


def _hostname(value: str, what: str) -> str:
    """A lower-cased, punycoded DNS name with at least two labels that is not an IP address."""
    try:
        host = value.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"{what} is not a valid hostname") from None
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_DOMAIN_LABEL.fullmatch(p) for p in labels):
        raise ValueError(f"{what} is not a valid hostname")
    if labels[-1].isdigit():
        raise ValueError(f"{what} must not be an IP address")
    return host


EmailAddress = Annotated[
    str,
    AfterValidator(normalise_email),
    Field(max_length=320, json_schema_extra={"format": "email"}),
]


class VerifyEmailIn(BaseModel):
    email: EmailAddress


class VerifyEmailOut(BaseModel):
    email: str
    status: VerifyStatus
    sub_status: str | None = None
    provider: str
    checked_at: AwareDatetime


def is_definitive(status: VerifyStatus) -> bool:
    """A real verdict, as opposed to ``unknown`` (both providers refund unknown results)."""
    return status != "unknown"


# --- shared building blocks ---------------------------------------------------------------------------


class Source(BaseModel):
    """Who answered: the provider pool and the connection (account) inside it."""

    provider: str
    connection_id: str


class Sourced(BaseModel):
    """Base of every output model added after ``verify_email``: provenance and freshness are mandatory."""

    source: Source
    observed_at: AwareDatetime


class _Input(BaseModel):
    """Base of the input models: a misspelt parameter is an error, not a silently ignored default."""

    model_config = ConfigDict(extra="forbid")


def normalise_domain(value: str) -> str:
    """``https://www.Acme.com/about`` -> ``acme.com``. Accepts a bare domain, a URL or a ``www.`` host."""
    text = value.strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = re.split(r"[/?#]", text, maxsplit=1)[0]
    if "@" in text:
        raise ValueError("domain must not contain '@' (give the company domain, not an email address)")
    if ":" in text:
        raise ValueError("domain must not include a port")
    text = text.removeprefix("www.")
    return _hostname(text, "domain")


Domain = Annotated[str, AfterValidator(normalise_domain), Field(max_length=300)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(min_length=1, max_length=100_000)]


# --- find_person --------------------------------------------------------------------------------------

Seniority = Literal[
    "owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager", "senior", "entry", "intern"
]


class FindPersonIn(_Input):
    company_domain: Domain | None = None
    company_name: ShortText | None = None
    titles: list[ShortText] = Field(default_factory=list, max_length=20)
    seniority: list[Seniority] = Field(default_factory=list, max_length=11)
    limit: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def _needs_a_company(self) -> FindPersonIn:
        if self.company_domain is None and self.company_name is None:
            raise ValueError("give company_domain or company_name")
        return self


class PersonHit(BaseModel):
    provider_person_id: str | None = None
    name: str
    first_name: str | None = None
    last_name: str | None = None
    # Apollo's free search masks the surname ("Hu***n"); a consumer must not treat that as a real name.
    name_is_partial: bool = False
    title: str | None = None
    company: str | None = None
    linkedin: str | None = None
    has_email: bool | None = None


class FindPersonOut(Sourced):
    people: list[PersonHit]
    total: int | None = None


# --- find_email ---------------------------------------------------------------------------------------

PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class FindEmailIn(_Input):
    first_name: PersonName
    last_name: PersonName
    domain: Domain


class FindEmailOut(Sourced):
    first_name: str
    last_name: str
    domain: str
    email: str | None = None
    confidence: int | None = Field(default=None, ge=0, le=100)
    # The provider's own check of the address, in the Farm's verify_email vocabulary, plus its raw label.
    verification: VerifyStatus | None = None
    verification_detail: str | None = None
    position: str | None = None
    linkedin: str | None = None


# --- enrich_company -----------------------------------------------------------------------------------


class EnrichCompanyIn(_Input):
    domain: Domain
    name: ShortText | None = None


class Socials(BaseModel):
    linkedin: str | None = None
    twitter: str | None = None
    facebook: str | None = None


class CompanyLocation(BaseModel):
    city: str | None = None
    region: str | None = None
    country: str | None = None


_SIZE_BUCKETS: tuple[tuple[int, str], ...] = (
    (10, "1-10"),
    (50, "11-50"),
    (200, "51-200"),
    (500, "201-500"),
    (1000, "501-1000"),
    (5000, "1001-5000"),
    (10000, "5001-10000"),
)


def employee_size_range(count: int | None) -> str | None:
    """The conventional headcount bucket for an employee count (``None`` when the count is unknown)."""
    if count is None or count < 1:
        return None
    for upper, label in _SIZE_BUCKETS:
        if count <= upper:
            return label
    return "10001+"


class EnrichCompanyOut(Sourced):
    domain: str
    name: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    size_range: str | None = None
    location: CompanyLocation = Field(default_factory=CompanyLocation)
    socials: Socials = Field(default_factory=Socials)
    tech_hints: list[str] = Field(default_factory=list)
    description: str | None = None
    founded_year: int | None = None
    website: str | None = None


# --- pagespeed ----------------------------------------------------------------------------------------


def normalise_http_url(value: str) -> str:
    """An absolute http(s) URL without embedded credentials (they would be forwarded to the provider)."""
    text = value.strip()
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("url must be an absolute http:// or https:// URL")
    if parts.username or parts.password:
        raise ValueError("url must not contain credentials")
    return text


WebUrl = Annotated[str, AfterValidator(normalise_http_url), Field(max_length=2048)]
PageStrategy = Literal["mobile", "desktop"]
Rating = Literal["good", "needs_improvement", "poor"]


class PageSpeedIn(_Input):
    url: WebUrl
    strategy: PageStrategy = "mobile"


class FieldMetrics(BaseModel):
    """Real-user data (Chrome UX Report) at the 75th percentile, as reported by PageSpeed Insights."""

    kind: Literal["field"] = "field"
    scope: Literal["page", "origin"]  # "origin" when CrUX had too little data for the page itself
    as_of: AwareDatetime  # the PSI analysis time; the API does not date the CrUX collection window
    lcp_ms: float | None = None
    inp_ms: float | None = None
    cls: float | None = None
    lcp_rating: Rating | None = None
    inp_rating: Rating | None = None
    cls_rating: Rating | None = None


class LabMetrics(BaseModel):
    """One synthetic Lighthouse run. Lab numbers vary run to run and are not what users experienced."""

    kind: Literal["lab"] = "lab"
    performance_score: int | None = Field(default=None, ge=0, le=100)
    lcp_ms: float | None = None
    cls: float | None = None
    tbt_ms: float | None = None  # lab proxy for responsiveness; INP exists only as field data
    fcp_ms: float | None = None
    speed_index_ms: float | None = None
    lighthouse_version: str | None = None


class PageSpeedOut(Sourced):
    url: str
    final_url: str | None = None
    strategy: PageStrategy
    field: FieldMetrics | None = None  # None when CrUX has no data for the page or its origin
    lab: LabMetrics


# --- jobs_lookup --------------------------------------------------------------------------------------

JobsPlatform = Literal["greenhouse", "lever", "ashby", "adzuna"]
AtsName = Literal["greenhouse", "lever", "ashby"]
BoardToken = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
]


def _country_code(value: str) -> str:
    code = value.strip().lower()
    if not re.fullmatch(r"[a-z]{2}", code):
        raise ValueError("country must be a two-letter code such as 'us' or 'gb'")
    return code


CountryCode = Annotated[str, AfterValidator(_country_code)]


class JobsLookupIn(_Input):
    """Postings of one company (``board`` = its job-board token, or ``company`` = its name) or a search
    (``what``/``where``/``country``)."""

    board: BoardToken | None = None
    company: ShortText | None = None
    ats: AtsName | None = None
    what: ShortText | None = None
    where: ShortText | None = None
    country: CountryCode = "us"
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def _needs_a_target(self) -> JobsLookupIn:
        if not (self.board or self.company or self.what or self.where):
            raise ValueError("give board, company, what or where")
        if self.ats and not (self.board or self.company):
            raise ValueError("ats only applies together with board or company")
        return self


class JobPosting(BaseModel):
    id: str
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    posted_at: AwareDatetime | None = None
    department: str | None = None
    employment_type: str | None = None
    remote: bool | None = None
    salary_min: float | None = None  # annual, in the posting's own currency (not normalised)
    salary_max: float | None = None
    platform: JobsPlatform


class JobsLookupOut(Sourced):
    postings: list[JobPosting]
    total: int | None = None  # how many the provider holds in all; may exceed len(postings)
    platform: JobsPlatform | None = None  # which job-board platform / provider answered
    board: str | None = None
    board_guessed: bool = False  # True when ``board`` was derived from ``company`` and not given


# --- extract / classify (LLM through Bifrost) ---------------------------------------------------------


def check_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Validate that ``schema`` is a usable JSON Schema (draft 2020-12). Only local ``$ref``s are allowed."""
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ValueError(f"json_schema is not a valid JSON Schema: {exc.message}") from None
    if schema.get("type") != "object":
        raise ValueError('json_schema must describe a JSON object ("type": "object")')
    if any(not ref.startswith("#") for ref in _refs(schema)):
        raise ValueError("json_schema may only use local $ref values ('#/...')")
    if len(json.dumps(schema)) > 20_000:
        raise ValueError("json_schema is larger than 20,000 characters")
    return schema


def _refs(node: object) -> list[str]:
    if isinstance(node, dict):
        found = [node["$ref"]] if isinstance(node.get("$ref"), str) else []
        return found + [ref for value in node.values() for ref in _refs(value)]
    if isinstance(node, list):
        return [ref for value in node for ref in _refs(value)]
    return []


def json_schema_errors(instance: object, schema: dict[str, Any], *, limit: int = 5) -> list[str]:
    """Why ``instance`` does not satisfy ``schema``, as short ``path: message`` lines (empty = valid)."""
    validator = jsonschema.Draft202012Validator(schema)
    problems: list[str] = []
    for error in sorted(validator.iter_errors(instance), key=lambda e: [str(p) for p in e.absolute_path]):
        where = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{where}: {str(error.message)[:200]}")
        if len(problems) >= limit:
            break
    return problems


class ExtractIn(_Input):
    text: LongText
    json_schema: Annotated[dict[str, Any], AfterValidator(check_json_schema)]


class ExtractOut(Sourced):
    data: dict[str, Any]
    model: str
    attempts: int = Field(ge=1, le=2)  # 2 = the first answer was invalid and one repair call fixed it


Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class ClassifyIn(_Input):
    text: LongText
    labels: list[Label] = Field(min_length=2, max_length=50)

    @model_validator(mode="after")
    def _labels_unique(self) -> ClassifyIn:
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("labels must be unique")
        return self


class ClassifyOut(Sourced):
    label: str
    confidence: float = Field(ge=0, le=1)
    # the model's own estimate, not a calibrated probability
    confidence_basis: Literal["self_reported"] = "self_reported"
    model: str
    attempts: int = Field(ge=1, le=2)


# --- capability -> (input model, output model) --------------------------------------------------------

CAPABILITY_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "verify_email": (VerifyEmailIn, VerifyEmailOut),
    "find_person": (FindPersonIn, FindPersonOut),
    "find_email": (FindEmailIn, FindEmailOut),
    "enrich_company": (EnrichCompanyIn, EnrichCompanyOut),
    "pagespeed": (PageSpeedIn, PageSpeedOut),
    "jobs_lookup": (JobsLookupIn, JobsLookupOut),
    "extract": (ExtractIn, ExtractOut),
    "classify": (ClassifyIn, ClassifyOut),
}


def _key(raw: object) -> str:
    return str(raw).strip().lower().replace("-", "_").replace(" ", "_")


def _sub(raw: object) -> str | None:
    if raw is None:
        return None
    text = _key(raw)
    return text or None


# --- Reoon ------------------------------------------------------------------------------------------
# https://www.reoon.com/articles/api-documentation-of-reoon-email-verifier/
# https://www.reoon.com/articles/meaning-of-different-email-verification-statuses/

REOON_POWER_STATUS: dict[str, Mapped] = {
    "safe": ("valid", None),
    "invalid": ("invalid", None),
    "disabled": ("invalid", "disabled"),
    "disposable": ("risky", "disposable"),
    "inbox_full": ("risky", "inbox_full"),
    "catch_all": ("catch_all", None),
    "role_account": ("risky", "role_based"),
    "spamtrap": ("risky", "spamtrap"),
    "unknown": ("unknown", None),
}

# Quick mode never checks the individual inbox: Reoon says every address on a good domain comes back
# "valid", including non-existent ones. That is not a verdict on the mailbox, so it is never "valid" here.
REOON_QUICK_STATUS: dict[str, Mapped] = {
    "valid": ("risky", "mailbox_unchecked"),
    "invalid": ("invalid", None),
    "disposable": ("risky", "disposable"),
    "spamtrap": ("risky", "spamtrap"),
}


def map_reoon_status(raw: object, mode: str) -> Mapped | None:
    """Map a Reoon ``status`` for ``mode`` ('quick' | 'power'); ``None`` when Reoon sent something new."""
    table = REOON_QUICK_STATUS if mode == "quick" else REOON_POWER_STATUS
    return table.get(_key(raw))


# --- Hunter -----------------------------------------------------------------------------------------
# https://hunter.io/api-documentation/v2#email-verifier

HUNTER_STATUS: dict[str, Mapped] = {
    "valid": ("valid", None),
    "invalid": ("invalid", None),
    "accept_all": ("catch_all", None),
    # Hunter does not probe the mailbox of webmail providers (it reports an arbitrary score of 50), so the
    # address was never actually checked: risky, like Reoon's quick-mode "valid".
    "webmail": ("risky", "mailbox_unchecked"),
    "disposable": ("risky", "disposable"),
    "unknown": ("unknown", None),
}


def map_hunter_status(raw: object) -> Mapped | None:
    """Map a Hunter ``status``; ``None`` when Hunter sent a status we don't know."""
    return HUNTER_STATUS.get(_key(raw))


# --- ZeroBounce -------------------------------------------------------------------------------------
# https://www.zerobounce.net/docs/email-validation-api-quickstart/v2-status-codes

ZEROBOUNCE_STATUS: dict[str, VerifyStatus] = {
    "valid": "valid",
    "invalid": "invalid",
    "catch_all": "catch_all",
    "unknown": "unknown",
    # "do not mail" bundles disposable, role-based, toxic, suppression-list and trap-like addresses:
    # real or plausible mailboxes that should not be mailed blindly.
    "do_not_mail": "risky",
    "spamtrap": "risky",
    "abuse": "risky",
}

# ZeroBounce files a full mailbox under "invalid"; Reoon (rightly) calls it a live but full inbox.
# Normalise both the same way so a provider fallback cannot flip the verdict.
_ZEROBOUNCE_SUB_OVERRIDES: dict[tuple[str, str], VerifyStatus] = {
    ("invalid", "mailbox_quota_exceeded"): "risky",
}


def map_zerobounce_status(raw: object, sub_status: object = None) -> Mapped | None:
    """Map a ZeroBounce ``status``/``sub_status``; ``None`` when ZeroBounce sent a status we don't know."""
    status_key = _key(raw)
    base = ZEROBOUNCE_STATUS.get(status_key)
    if base is None:
        return None
    sub = _sub(sub_status)
    status = _ZEROBOUNCE_SUB_OVERRIDES.get((status_key, sub or ""), base)
    if sub is None and status_key in {"spamtrap", "abuse"}:
        sub = status_key  # these carry no sub_status of their own; keep the reason visible
    return status, sub
