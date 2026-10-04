"""Capability-level routing policy that the registry does not carry.

``falls_back_on_empty``: when a provider answers "nothing found" (``ok=True, found=False, error_kind=EMPTY``,
see ``farm.adapters._template.empty_result``) the call is complete and charged as the provider reports it;
this says whether the router then also asks the next pool of the route.

* look-ups for something that may exist at another source fall back: ``find_*`` (default by prefix) and, per
  the capability graph in HANDOFF 4.2, ``enrich_company`` (own crawl -> Apollo -> Clay) and ``jobs_lookup``
  (public ATS boards -> Adzuna);
* verdicts and single-source capabilities do not: ``verify_email`` (an inconclusive verdict from Reoon is
  not retried at another vendor), ``pagespeed``, ``extract``, ``classify``.
"""

from __future__ import annotations

EMPTY_FALLBACK: dict[str, bool] = {
    "enrich_company": True,
    "jobs_lookup": True,
}
"""Explicit overrides of the default (``find_*`` -> True, everything else -> False)."""


def falls_back_on_empty(capability: str) -> bool:
    explicit = EMPTY_FALLBACK.get(capability)
    return capability.startswith("find_") if explicit is None else explicit
