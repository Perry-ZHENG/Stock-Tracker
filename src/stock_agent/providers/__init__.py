"""Market data provider adapters.

Broker compatibility exports remain lazy so the V2 read-only research import
graph cannot load broker modules by accident.
"""

from typing import Any
from stock_agent.providers.csv_demo import CsvDemoProvider, CsvDemoProviderError
from stock_agent.providers.live import (
    AlphaVantageProvider,
    LiveProviderError,
    LiveProviderLimits,
    create_live_provider,
)
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
    "AlphaVantageProvider",
    "BrokerMarketDataProvider",
    "BrokerMarketDataProviderError",
    "CsvDemoProvider",
    "CsvDemoProviderError",
    "LiveProviderError",
    "LiveProviderLimits",
    "ProviderAttempt",
    "ProviderFetchResult",
    "ProviderRegistry",
    "ProviderRegistryError",
    "SyntheticDemoProviderError",
    "SyntheticDemoProviderV2",
    "TwelveDataProvider",
    "TwelveDataProviderError",
    "create_broker_market_data_provider",
    "create_live_provider",
    "create_twelve_data_provider",
]


def __getattr__(name: str) -> Any:
    if name in {
        "BrokerMarketDataProvider",
        "BrokerMarketDataProviderError",
        "create_broker_market_data_provider",
    }:
        from stock_agent.providers import broker_market_data

        return getattr(broker_market_data, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
