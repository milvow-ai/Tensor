"""API adapters, one module per provider. ``ADAPTERS`` maps provider id (registry key) -> adapter class."""

from farm.adapters._template import ApiAdapter, BalanceResult, ProviderError
from farm.adapters.reoon import ReoonAdapter
from farm.adapters.zerobounce import ZeroBounceAdapter

ADAPTERS: dict[str, type[ApiAdapter]] = {
    ReoonAdapter.provider_id: ReoonAdapter,
    ZeroBounceAdapter.provider_id: ZeroBounceAdapter,
}

__all__ = [
    "ADAPTERS",
    "ApiAdapter",
    "BalanceResult",
    "ProviderError",
    "ReoonAdapter",
    "ZeroBounceAdapter",
]
