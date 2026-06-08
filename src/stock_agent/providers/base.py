"""Base interfaces for market data providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Literal

from stock_agent.schemas import Bar


class MarketDataProvider(ABC):
    """Shared interface for market data provider adapters."""

    @abstractmethod
    def fetch_intraday_bars(
        self,
        symbols: list[str] | None = None,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        """Return standardized intraday bars."""

    def fetch_market_status(self) -> dict[str, str]:
        return {"provider": self.__class__.__name__, "status": "unknown"}

    def fetch_daily_bars(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        adjusted: bool = True,
    ) -> list[Bar]:
        raise NotImplementedError("Daily bars are not implemented for this provider.")

    def stream_quotes(self, symbols: list[str]) -> None:
        raise NotImplementedError("Streaming quotes are not implemented for this provider.")

    def fetch_provider_health(self) -> dict[str, str | int | float]:
        return {"provider": self.__class__.__name__, "status": "healthy"}


ProviderName = Literal["csv_demo", "alpha_vantage"]
