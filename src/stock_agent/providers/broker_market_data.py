"""Broker-backed market data provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from stock_agent.broker import BrokerAdapter
from stock_agent.providers.base import MarketDataProvider
from stock_agent.schemas import Bar
from stock_agent.security import redact_sensitive

BrokerEnvironment = Literal["sandbox", "paper", "live"]


class BrokerMarketDataProviderError(RuntimeError):
    """Raised when broker market data cannot be used safely."""


@dataclass(frozen=True)
class BrokerMarketDataProvider(MarketDataProvider):
    adapter: BrokerAdapter
    environment: BrokerEnvironment = "sandbox"
    enabled: bool = False

    def fetch_intraday_bars(
        self,
        symbols: list[str] | None = None,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        if not self.enabled:
            raise BrokerMarketDataProviderError(f"broker market data provider is not configured; environment={self.environment}")
        if self.environment == "live":
            raise BrokerMarketDataProviderError("live broker market data is disabled by default")
        if self.adapter.capabilities.has_trading_or_mutation_permissions:
            raise BrokerMarketDataProviderError(
                "broker adapter declares trading or account mutation permissions; broker provider is disabled by default"
            )
        if not self.adapter.capabilities.market_data:
            raise BrokerMarketDataProviderError("broker adapter does not declare market_data capability")
        bars = self.adapter.fetch_market_data(symbols=symbols, interval=interval, start=start, end=end)
        return [Bar.model_validate(bar.model_dump(mode="json")) for bar in bars]

    def fetch_provider_health(self) -> dict[str, str | int | float]:
        return redact_sensitive({
            "provider": "broker_market_data",
            "environment": self.environment,
            "enabled": int(self.enabled),
            "market_data_capability": int(self.adapter.capabilities.market_data),
            "trading_permissions_detected": int(self.adapter.capabilities.has_trading_or_mutation_permissions),
        })


def create_broker_market_data_provider(
    *,
    adapter: BrokerAdapter | None = None,
    environment: BrokerEnvironment = "sandbox",
    enabled: bool = False,
) -> BrokerMarketDataProvider:
    if adapter is None:
        raise BrokerMarketDataProviderError(f"broker adapter is not configured; environment={environment}")
    return BrokerMarketDataProvider(adapter=adapter, environment=environment, enabled=enabled)


__all__ = [
    "BrokerEnvironment",
    "BrokerMarketDataProvider",
    "BrokerMarketDataProviderError",
    "create_broker_market_data_provider",
]
