"""Read-only market data providers used by V2 research tasks."""

from stock_agent.providers.synthetic_demo_v2 import SyntheticDemoProviderError, SyntheticDemoProviderV2
from stock_agent.providers.registry import (
    ProviderAttempt,
    ProviderFetchResult,
    ProviderRegistry,
    ProviderRegistryError,
)
from stock_agent.providers.twelve_data import (
    TwelveDataProvider,
    TwelveDataProviderError,
    create_twelve_data_provider,
)

__all__ = [
    "ProviderAttempt",
    "ProviderFetchResult",
    "ProviderRegistry",
    "ProviderRegistryError",
    "SyntheticDemoProviderError",
    "SyntheticDemoProviderV2",
    "TwelveDataProvider",
    "TwelveDataProviderError",
    "create_twelve_data_provider",
]
