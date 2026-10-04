"""API adapters, one module per provider. ``ADAPTERS`` maps provider id (registry key) -> adapter class.

The LLM executor (``farm.executors.llm``) is not listed: its registry providers use ``executor: llm``, not
``executor: api``.
"""

from farm.adapters._template import ApiAdapter, BalanceResult, ProviderError
from farm.adapters.adzuna import AdzunaAdapter
from farm.adapters.apollo import ApolloAdapter
from farm.adapters.ats_public import AtsPublicAdapter
from farm.adapters.hunter import HunterAdapter
from farm.adapters.pagespeed import PageSpeedAdapter
from farm.adapters.reoon import ReoonAdapter
from farm.adapters.zerobounce import ZeroBounceAdapter

ADAPTERS: dict[str, type[ApiAdapter]] = {
    adapter.provider_id: adapter
    for adapter in (
        ReoonAdapter,
        ZeroBounceAdapter,
        ApolloAdapter,
        HunterAdapter,
        PageSpeedAdapter,
        AdzunaAdapter,
        AtsPublicAdapter,
    )
}

__all__ = [
    "ADAPTERS",
    "AdzunaAdapter",
    "ApiAdapter",
    "ApolloAdapter",
    "AtsPublicAdapter",
    "BalanceResult",
    "HunterAdapter",
    "PageSpeedAdapter",
    "ProviderError",
    "ReoonAdapter",
    "ZeroBounceAdapter",
]
