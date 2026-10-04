"""Google PageSpeed Insights API v5: ``pagespeed`` (``runPagespeed``).

Docs (fetched 2026-10-04):
  https://developers.google.com/speed/docs/insights/v5/get-started   (API key as ``key=``, sample response)
  https://developers.google.com/speed/docs/insights/rest/v5/pagespeedapi/runpagespeed   (parameters, schema)
  https://developers.google.com/speed/docs/insights/v5/about   (CrUX metrics, 75th percentile, ratings)
  https://cloud.google.com/apis/design/errors   (the Google error envelope ``error.status``/``details``)

* The key travels as ``key=`` (documented as safe to embed in URLs; the template still masks it in errors
  and logs). ``strategy`` is ``mobile`` or ``desktop``; with no ``category`` only Performance runs.
* The result has two kinds of numbers and the output keeps them apart: ``field`` is real-user data from the
  Chrome UX Report at the 75th percentile (LCP, INP, CLS) and ``lab`` is one synthetic Lighthouse run. When
  CrUX has too little data for the page, PSI answers with origin-level data (``origin_fallback``) and the
  adapter reports ``scope="origin"``; with no CrUX data at all ``field`` is ``None``. Google says it plans to
  stop returning CrUX data from this API (the CrUX API replaces it), so ``field`` may disappear one day.
* PSI reports the CLS percentile multiplied by 100 (its ``distributions`` bounds are 10 and 25 for the 0.1
  and 0.25 thresholds). The docs pages above do not state that, so it is UNVERIFIED against the docs and
  covered by a fixture that follows the observed convention.
* Charging: one ``requests`` unit per call (the daily quota counts requests). A request that Lighthouse
  could not run (``runtimeError``, or a 4xx/5xx error) is reported as a failure.
* Errors use Google's envelope ``{"error": {"code", "message", "status", "errors": [{"reason"}], "details":
  [{"reason", "metadata"}]}}``. A bad key (``API_KEY_INVALID``, ``PERMISSION_DENIED``) is AUTH; a daily
  quota (``dailyLimitExceeded``, ``PerDay`` in the quota name) is LIMIT_REACHED; any other
  ``RESOURCE_EXHAUSTED`` is RATE_LIMITED; a Lighthouse failure that is the target page's fault
  (``FAILED_DOCUMENT_REQUEST``, ``ERRORED_DOCUMENT_REQUEST``, ``NOT_HTML``, DNS) is BAD_REQUEST, as retrying
  the same URL cannot help. The envelope's reasons are from Google's general error design and the
  Lighthouse wording is from memory of public reports: both are UNVERIFIED against recorded output.
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
    as_list,
    as_text,
    clip,
    parse_params,
)
from farm.capabilities.schemas import FieldMetrics, LabMetrics, PageSpeedIn, PageSpeedOut, Rating
from farm.executors.base import ErrorKind, ExecRequest, ExecResult

_RATINGS: dict[str, Rating] = {"FAST": "good", "AVERAGE": "needs_improvement", "SLOW": "poor"}
_AUTH_REASONS = {
    "api_key_invalid",
    "api_key_expired",
    "keyinvalid",
    "keyexpired",
    "accessnotconfigured",
    "service_disabled",
    "api_key_service_blocked",
    "forbidden",
}
_QUOTA_REASONS = {
    "ratelimitexceeded",
    "userratelimitexceeded",
    "dailylimitexceeded",
    "quotaexceeded",
    "rate_limit_exceeded",
    "resource_exhausted",
}
_BAD_TARGET = (
    "FAILED_DOCUMENT_REQUEST",
    "ERRORED_DOCUMENT_REQUEST",
    "NOT_HTML",
    "DNS_FAILURE",
    "INVALID_URL",
)


def _error_facts(body: Any) -> tuple[str, str, set[str], str]:
    """(message, google status, lower-cased reasons, text of every quota/metadata value)."""
    error = as_dict(as_dict(body).get("error"))
    reasons: set[str] = set()
    quota_text: list[str] = []
    for entry in as_list(error.get("errors")):
        if reason := as_text(as_dict(entry).get("reason")):
            reasons.add(reason.lower())
    for detail in as_list(error.get("details")):
        info = as_dict(detail)
        if reason := as_text(info.get("reason")):
            reasons.add(reason.lower())
        quota_text.extend(str(v) for v in as_dict(info.get("metadata")).values())
    return (
        as_text(error.get("message")) or "",
        (as_text(error.get("status")) or "").upper(),
        reasons,
        " ".join(quota_text),
    )


def _rating_and_percentile(metrics: dict[str, Any], key: str) -> tuple[float | None, Rating | None]:
    metric = as_dict(metrics.get(key))
    return as_float(metric.get("percentile")), _RATINGS.get((as_text(metric.get("category")) or "").upper())


def _parse_time(value: object, fallback: datetime) -> datetime:
    text = as_text(value)
    if text is None:
        return fallback
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class PageSpeedAdapter(ApiAdapter):
    provider_id = "pagespeed"
    base_url = "https://pagespeedonline.googleapis.com"

    def capabilities(self) -> Mapping[str, Handler]:
        return {"pagespeed": self._pagespeed}

    def _error_from_response(
        self, status_code: int, body: Any, retry_after_s: float | None
    ) -> ProviderError | None:
        by_status = super()._error_from_response(status_code, body, retry_after_s)
        if by_status is None:
            return None
        message, google_status, reasons, quota_text = _error_facts(body)
        kind = by_status.kind
        lowered = f"{message} {quota_text}".lower()
        if google_status in ("UNAUTHENTICATED", "PERMISSION_DENIED") or reasons & _AUTH_REASONS:
            kind = ErrorKind.AUTH
        elif "api key not valid" in lowered or "api key expired" in lowered:
            kind = ErrorKind.AUTH
        elif google_status == "RESOURCE_EXHAUSTED" or reasons & _QUOTA_REASONS:
            daily = "dailylimitexceeded" in reasons or "per day" in lowered or "perday" in lowered
            kind = ErrorKind.LIMIT_REACHED if daily else ErrorKind.RATE_LIMITED
        elif message.startswith("Lighthouse returned error") and any(code in message for code in _BAD_TARGET):
            kind = ErrorKind.BAD_REQUEST
        detail = f"HTTP {status_code}" + (f": {clip(message)}" if message else "")
        return ProviderError(kind, detail, retry_after_s=retry_after_s)

    async def _pagespeed(self, req: ExecRequest, secret: str) -> ExecResult:
        params = parse_params(PageSpeedIn, req.params, "pagespeed")
        body = await self._get_json(
            "/pagespeedonline/v5/runPagespeed",
            params={"url": params.url, "strategy": params.strategy, "key": secret},
            timeout_s=req.timeout_s,
        )
        if not isinstance(body, dict):
            raise ProviderError(ErrorKind.UNKNOWN, "PageSpeed response is not an object")
        lighthouse = as_dict(body.get("lighthouseResult"))
        if not lighthouse:
            raise ProviderError(ErrorKind.UNKNOWN, "PageSpeed response has no 'lighthouseResult'")
        runtime_error = as_dict(lighthouse.get("runtimeError"))
        if runtime_error:
            code = as_text(runtime_error.get("code")) or "UNKNOWN"
            kind = ErrorKind.TIMEOUT if "TIMEOUT" in code.upper() else ErrorKind.BAD_REQUEST
            reason = clip(as_text(runtime_error.get("message")) or "")
            raise ProviderError(kind, f"Lighthouse could not analyse the page ({clip(code, 60)}): {reason}")

        observed_at = self._now()
        analysed_at = _parse_time(body.get("analysisUTCTimestamp"), observed_at)
        lab = self._lab(lighthouse)
        out = PageSpeedOut(
            source=self._source(req),
            observed_at=observed_at,
            url=params.url,
            final_url=as_text(lighthouse.get("finalUrl")) or as_text(body.get("id")),
            strategy=params.strategy,
            field=self._field(body, analysed_at),
            lab=lab,
        )
        return ExecResult(ok=True, data=out.model_dump(mode="json"), found=True, units_used={"requests": 1.0})

    @staticmethod
    def _lab(lighthouse: dict[str, Any]) -> LabMetrics:
        audits = as_dict(lighthouse.get("audits"))

        def numeric(audit_id: str) -> float | None:
            return as_float(as_dict(audits.get(audit_id)).get("numericValue"))

        score = as_float(as_dict(as_dict(lighthouse.get("categories")).get("performance")).get("score"))
        lab = LabMetrics(
            performance_score=None if score is None else max(0, min(100, round(score * 100))),
            lcp_ms=numeric("largest-contentful-paint"),
            cls=numeric("cumulative-layout-shift"),
            tbt_ms=numeric("total-blocking-time"),
            fcp_ms=numeric("first-contentful-paint"),
            speed_index_ms=numeric("speed-index"),
            lighthouse_version=as_text(lighthouse.get("lighthouseVersion")),
        )
        if all(
            value is None
            for value in (
                lab.performance_score,
                lab.lcp_ms,
                lab.cls,
                lab.tbt_ms,
                lab.fcp_ms,
                lab.speed_index_ms,
            )
        ):
            raise ProviderError(ErrorKind.UNKNOWN, "PageSpeed response has no Lighthouse metrics")
        return lab

    @staticmethod
    def _field(body: dict[str, Any], analysed_at: datetime) -> FieldMetrics | None:
        page = as_dict(body.get("loadingExperience"))
        origin = as_dict(body.get("originLoadingExperience"))
        if as_dict(page.get("metrics")):
            experience, scope = page, ("origin" if page.get("origin_fallback") is True else "page")
        elif as_dict(origin.get("metrics")):
            experience, scope = origin, "origin"
        else:
            return None
        metrics = as_dict(experience.get("metrics"))
        lcp, lcp_rating = _rating_and_percentile(metrics, "LARGEST_CONTENTFUL_PAINT_MS")
        inp, inp_rating = _rating_and_percentile(metrics, "INTERACTION_TO_NEXT_PAINT")
        cls_x100, cls_rating = _rating_and_percentile(metrics, "CUMULATIVE_LAYOUT_SHIFT_SCORE")
        if lcp is None and inp is None and cls_x100 is None:
            return None
        return FieldMetrics(
            scope="origin" if scope == "origin" else "page",
            as_of=analysed_at,
            lcp_ms=lcp,
            inp_ms=inp,
            cls=None if cls_x100 is None else cls_x100 / 100,
            lcp_rating=lcp_rating,
            inp_rating=inp_rating,
            cls_rating=cls_rating,
        )
