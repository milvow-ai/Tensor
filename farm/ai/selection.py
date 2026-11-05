"""Explicit model / effort choice for AI work: what an account offers, and how a request's choice resolves.

The operator (an MCP client or the Console) picks a model and, where the provider supports it, an effort
before any work starts. Nothing is implied: a request without a choice is refused with ``selection_required``
and the menu (``farm.ai.accounts.options_menu`` / the ``ai_options`` tool), before a job row, a quota
reservation or a CLI exists. An account's default choice applies only when the request says
``use_defaults = true`` *and* the account has opted in with
``meta.selection_defaults = {"enabled": true, "model": ..., "effort": ...}`` whose values it actually offers.
The legacy keys ``meta.model`` / ``default_model`` / ``effort`` / ``default_effort`` never count as that
opt-in (``meta.model`` still lists a configured model, nothing more).

Models are reported with their source: ``configured`` (``meta.models``, written by the operator) or
``verified`` (also in ``meta.verified_models``, written by a discovery that asked the CLI). A configured name
may be a CLI alias (``opus``); it is never presented as the exact provider model the CLI resolved it to.

This module is pure (no database, no ``farm`` import at all), so the router, the CLI drivers and the schemas
can use it, and the router enforces the same rule on the blocking ``ask_ai`` path that ``JobManager`` enforces
before it creates a job.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

SelectionSource = Literal["explicit", "account_defaults", "inherited"]
AI_EFFORTS: Final[tuple[str, ...]] = ("low", "medium", "high", "xhigh", "max")

EFFORT_PROVIDERS: Final[Mapping[str, tuple[str, ...]]] = {
    "claude": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("low", "medium", "high", "xhigh", "max"),
}
"""Efforts each CLI driver accepts, when an account's ``meta.efforts`` does not narrow them. Every other
provider's driver ignores effort: it is ``not_supported`` there, never a required pick."""

EFFORT_APPLIED_AS: Final[Mapping[str, Mapping[str, str]]] = {"codex": {"xhigh": "high", "max": "high"}}
"""What the driver really sends for a requested effort (the Codex driver caps at ``high``)."""


def supports_effort(provider: str) -> bool:
    return provider in EFFORT_PROVIDERS


def offered_efforts(provider: str, meta: Mapping[str, Any]) -> tuple[str, ...]:
    """The efforts an account accepts: ``meta.efforts`` (known values only) or the provider's (or none)."""
    if not supports_effort(provider):
        return ()
    raw = meta.get("efforts")
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, list):
        return tuple(e for e in (str(v) for v in raw) if e in AI_EFFORTS)
    return EFFORT_PROVIDERS[provider]


def effective_effort(provider: str, effort: str | None) -> str | None:
    """The effort the driver actually applies for a requested one (``None``: none is applied)."""
    if effort is None or not supports_effort(provider):
        return None
    return EFFORT_APPLIED_AS.get(provider, {}).get(effort, effort)


def _names(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    return tuple(dict.fromkeys(s for s in (str(v).strip() for v in raw) if s))


def configured_models(meta: Mapping[str, Any]) -> tuple[str, ...]:
    """``meta.models`` (or the single ``meta.model``): what the operator configured, in their order."""
    found = _names(meta.get("models"))
    return found or _names(meta.get("model"))


def model_menu(meta: Mapping[str, Any]) -> list[dict[str, str]]:
    """Each configured model with its source: ``verified`` (a discovery confirmed it) or ``configured``."""
    verified = {m.lower() for m in _names(meta.get("verified_models"))}
    return [
        {"id": m, "source": "verified" if m.lower() in verified else "configured"}
        for m in configured_models(meta)
    ]


def _offered(models: Sequence[str], model: str) -> str | None:
    """The configured spelling of ``model`` (compared case-insensitively), or ``None``."""
    return next((m for m in models if m.lower() == model.lower()), None)


@dataclass(frozen=True)
class Defaults:
    """An account's opt-in default choice. ``valid``: enabled and every value is one the account offers."""

    enabled: bool
    model: str | None
    effort: str | None
    problem: str | None

    @property
    def valid(self) -> bool:
        return self.enabled and self.problem is None

    def report(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.model,
            "effort": self.effort,
            "valid": self.valid,
            "problem": self.problem,
        }


def defaults_of(provider: str, meta: Mapping[str, Any]) -> Defaults:
    raw = meta.get("selection_defaults")
    if not isinstance(raw, Mapping) or raw.get("enabled") is not True:
        return Defaults(False, None, None, "defaults are not enabled for this account")
    model = raw.get("model") if isinstance(raw.get("model"), str) and raw.get("model") else None
    effort = raw.get("effort") if isinstance(raw.get("effort"), str) and raw.get("effort") else None
    problem: str | None = None
    if model is None:
        problem = "no default model configured"
    elif _offered(configured_models(meta), model) is None:
        problem = f"default model '{model}' is not one of the account's configured models"
    elif supports_effort(provider) and effort is None:
        problem = "no default effort configured"
    elif effort is not None and effort not in offered_efforts(provider, meta):
        problem = f"default effort '{effort}' is not offered by this account"
    return Defaults(True, model, effort if supports_effort(provider) else None, problem)


def defaults_config_problem(provider: str, meta: Mapping[str, Any]) -> str | None:
    """Why ``meta.selection_defaults`` must not be saved (``None``: absent or fine). Disabled defaults may
    keep their values; enabled ones must be valid now, so a broken default never reaches a request."""
    raw = meta.get("selection_defaults")
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or not isinstance(raw.get("enabled"), bool):
        return "selection_defaults needs a boolean 'enabled'"
    if set(raw) - {"enabled", "model", "effort"}:
        return "selection_defaults takes only enabled, model and effort"
    return defaults_of(provider, meta).problem if raw["enabled"] else None


@dataclass(frozen=True)
class Choice:
    """A resolved, validated choice: what is persisted on the job and passed to the driver."""

    model: str
    effort: str | None
    """The requested effort (``None`` for a provider without effort)."""
    effective_effort: str | None
    source: SelectionSource


@dataclass(frozen=True)
class Problem:
    kind: Literal["selection_required", "invalid_selection"]
    message: str
    missing: tuple[str, ...] = ()


def missing_fields(provider: str, *, model: str | None, effort: str | None) -> tuple[str, ...]:
    """What an explicit choice lacks for ``provider`` (``any``: only a model can be asked for up front)."""
    missing: list[str] = []
    if not model:
        missing.append("model")
    if effort is None and supports_effort(provider):
        missing.append("effort")
    return tuple(missing)


def resolve(
    provider: str,
    meta: Mapping[str, Any],
    *,
    model: str | None,
    effort: str | None,
    use_defaults: bool = False,
    inherited: bool = False,
) -> Choice | Problem:
    """The choice for one account, or why there is none. Explicit values win over the account's defaults,
    which fill gaps only with ``use_defaults`` and an enabled, valid opt-in."""
    source: SelectionSource = "inherited" if inherited else "explicit"
    missing = missing_fields(provider, model=model, effort=effort)
    if missing and use_defaults:
        defaults = defaults_of(provider, meta)
        if not defaults.valid:
            return Problem(
                "selection_required",
                f"use_defaults was asked for but {defaults.problem}; pick {' and '.join(missing)} explicitly",
                missing,
            )
        model = model or defaults.model
        if effort is None:
            effort = defaults.effort
        source = "account_defaults"
        missing = missing_fields(provider, model=model, effort=effort)
    if missing:
        return Problem("selection_required", f"choose {' and '.join(missing)} before starting", missing)
    assert model is not None
    offered = _offered(configured_models(meta), model)
    if offered is None:
        return Problem("invalid_selection", f"model '{model}' is not offered by this account")
    if effort is not None:
        if not supports_effort(provider):
            return Problem("invalid_selection", f"effort is not supported by {provider}")
        if effort not in offered_efforts(provider, meta):
            return Problem("invalid_selection", f"effort '{effort}' is not offered by this account")
    return Choice(offered, effort, effective_effort(provider, effort), source)


def account_menu(provider: str, meta: Mapping[str, Any]) -> dict[str, Any]:
    """One account's pick-list: models with source, efforts (or ``not_supported``), the default policy."""
    efforts: dict[str, Any]
    if supports_effort(provider):
        offered = offered_efforts(provider, meta)
        applied = {e: effective_effort(provider, e) for e in offered}
        efforts = {
            "supported": True,
            "values": list(offered),
            "applied_as": {e: a for e, a in applied.items() if a != e},
        }
    else:
        efforts = {"supported": False, "values": [], "note": "not_supported"}
    return {"models": model_menu(meta), "efforts": efforts, "defaults": defaults_of(provider, meta).report()}
