"""Live market data provider adapters."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from stock_agent.bars.validation import generate_bar_id
from stock_agent.providers.base import MarketDataProvider
from stock_agent.schemas import Bar

EASTERN = ZoneInfo("America/New_York")
HttpGet = Callable[[str, dict[str, str]], dict[str, Any]]


class LiveProviderError(RuntimeError):
    """Raised when a live market data provider cannot return standard bars."""


@dataclass(frozen=True)
class LiveProviderLimits:
    provider: str
    rate_limit_policy: str
    latency_policy: str
    fallback_policy: str = "raise LiveProviderError; caller should fall back to csv_demo/cache"


class AlphaVantageProvider(MarketDataProvider):
    """Alpha Vantage intraday adapter.

    Provider response structures are converted to standard `Bar` objects here so
    strategies never depend on vendor-specific keys.
    """

    source = "alpha_vantage"

    def __init__(
        self,
        *,
        api_key: str,
        http_get: HttpGet | None = None,
        base_url: str = "https://www.alphavantage.co/query",
    ) -> None:
        if not api_key:
            raise LiveProviderError("Alpha Vantage API key is required")
        self.api_key = api_key
        self.http_get = http_get or _urllib_get_json
        self.base_url = base_url
        self.limits = LiveProviderLimits(
            provider=self.source,
            rate_limit_policy="Use provider response throttling messages and local scheduling; free/premium quotas vary by key.",
            latency_policy="Historical intraday by default; realtime/delayed availability depends on entitlement.",
        )

    @classmethod
    def from_env(
        cls,
        api_key_env: str,
        *,
        http_get: HttpGet | None = None,
    ) -> "AlphaVantageProvider":
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise LiveProviderError(f"missing API key env {api_key_env}")
        return cls(api_key=api_key, http_get=http_get)

    def fetch_intraday_bars(
        self,
        symbols: list[str] | None = None,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        requested_symbols = symbols or []
        if not requested_symbols:
            raise LiveProviderError("Alpha Vantage requires at least one symbol")
        provider_interval = _to_alpha_vantage_interval(interval or "30m")
        standard_interval = _to_standard_interval(provider_interval)

        bars: list[Bar] = []
        for symbol in requested_symbols:
            payload = self.http_get(
                self.base_url,
                {
                    "function": "TIME_SERIES_INTRADAY",
                    "symbol": symbol,
                    "interval": provider_interval,
                    "adjusted": "true",
                    "extended_hours": "false",
                    "outputsize": "compact",
                    "datatype": "json",
                    "apikey": self.api_key,
                },
            )
            bars.extend(
                _parse_alpha_vantage_bars(
                    payload,
                    symbol=symbol,
                    standard_interval=standard_interval,
                    provider_interval=provider_interval,
                )
            )
        return [
            bar
            for bar in sorted(bars, key=lambda item: (item.symbol, item.timestamp))
            if _matches_filters(bar, symbols=requested_symbols, interval=standard_interval, start=start, end=end)
        ]

    def fetch_provider_health(self) -> dict[str, str | int | float]:
        return {
            "provider": self.source,
            "status": "configured",
            "rate_limit_policy": self.limits.rate_limit_policy,
            "latency_policy": self.limits.latency_policy,
        }


def create_live_provider(
    *,
    provider_name: str,
    api_key_env: str,
    http_get: HttpGet | None = None,
) -> MarketDataProvider:
    normalized = provider_name.lower()
    if normalized in {"alpha_vantage", "alphavantage"}:
        return AlphaVantageProvider.from_env(api_key_env, http_get=http_get)
    raise LiveProviderError(f"unsupported live provider: {provider_name}")


def _parse_alpha_vantage_bars(
    payload: dict[str, Any],
    *,
    symbol: str,
    standard_interval: str,
    provider_interval: str,
) -> list[Bar]:
    _raise_for_provider_error(payload)
    series_key = f"Time Series ({provider_interval})"
    series = payload.get(series_key)
    if not isinstance(series, dict):
        raise LiveProviderError(f"Alpha Vantage response missing {series_key}")

    bars: list[Bar] = []
    for timestamp_text, values in series.items():
        if not isinstance(values, dict):
            raise LiveProviderError(f"Alpha Vantage invalid bar payload for {symbol} at {timestamp_text}")
        timestamp = _parse_eastern_timestamp(timestamp_text)
        timestamp_id = timestamp.isoformat().replace("+00:00", "Z")
        try:
            bar = Bar(
                bar_id=generate_bar_id(symbol, standard_interval, timestamp_id, AlphaVantageProvider.source),
                symbol=symbol,
                timestamp=timestamp,
                interval=standard_interval,
                open=float(values["1. open"]),
                high=float(values["2. high"]),
                low=float(values["3. low"]),
                close=float(values["4. close"]),
                volume=int(float(values["5. volume"])),
                vwap=None,
                source=AlphaVantageProvider.source,
                quality_flag="normal",
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise LiveProviderError(
                f"Alpha Vantage invalid OHLCV payload for {symbol} at {timestamp_text}: {exc}"
            ) from exc
        bars.append(bar)
    return bars


def _raise_for_provider_error(payload: dict[str, Any]) -> None:
    for key in ("Error Message", "Note", "Information"):
        value = payload.get(key)
        if value:
            raise LiveProviderError(f"Alpha Vantage provider message: {value}")


def _parse_eastern_timestamp(timestamp_text: str) -> datetime:
    parsed = datetime.fromisoformat(timestamp_text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=EASTERN)
    return parsed.astimezone(ZoneInfo("UTC"))


def _to_alpha_vantage_interval(interval: str) -> str:
    mapping = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "60m": "60min",
        "1min": "1min",
        "5min": "5min",
        "15min": "15min",
        "30min": "30min",
        "60min": "60min",
    }
    try:
        return mapping[interval]
    except KeyError as exc:
        raise LiveProviderError(f"unsupported Alpha Vantage interval: {interval}") from exc


def _to_standard_interval(provider_interval: str) -> str:
    return provider_interval.replace("min", "m")


def _matches_filters(
    bar: Bar,
    *,
    symbols: list[str],
    interval: str,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if symbols and bar.symbol not in symbols:
        return False
    if bar.interval != interval:
        return False
    if start is not None and bar.timestamp < start:
        return False
    if end is not None and bar.timestamp > end:
        return False
    return True


def _urllib_get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "stock-agent/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


__all__ = [
    "AlphaVantageProvider",
    "LiveProviderError",
    "LiveProviderLimits",
    "create_live_provider",
]
