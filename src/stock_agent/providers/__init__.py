"""Market data provider adapters."""

from stock_agent.providers.broker_market_data import (
    BrokerMarketDataProvider,
    BrokerMarketDataProviderError,
    create_broker_market_data_provider,
)
from stock_agent.providers.csv_demo import CsvDemoProvider, CsvDemoProviderError
from stock_agent.providers.live import (
    AlphaVantageProvider,
    LiveProviderError,
    LiveProviderLimits,
    create_live_provider,
)
from stock_agent.providers.registry import (
    ProviderAttempt,
    ProviderFetchResult,
    ProviderRegistry,
    ProviderRegistryError,
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
    "create_broker_market_data_provider",
    "create_live_provider",
]
