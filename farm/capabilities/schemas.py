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
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, Field

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
    try:
        domain = domain.rstrip(".").lower().encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("email domain is not a valid hostname") from None
    labels = domain.split(".")
    if len(domain) > 253 or len(labels) < 2 or not all(_DOMAIN_LABEL.fullmatch(p) for p in labels):
        raise ValueError("email domain is not a valid hostname")
    if labels[-1].isdigit():
        raise ValueError("email domain must not be an IP address")
    return f"{local}@{domain}"


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
