"""Twelve Data REST market-data adapter."""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from stock_agent.bars.validation import generate_bar_id
from stock_agent.providers.base import MarketDataProvider
from stock_agent.schemas import Bar
from stock_agent.security import redact_sensitive

TwelveDataHttpGet = Callable[[str, dict[str, str], float], dict[str, Any]]
SleepFn = Callable[[float], None]


class TwelveDataProviderError(RuntimeError):
    """Raised when Twelve Data cannot return valid standard bars."""


class TwelveDataProvider(MarketDataProvider):
    source = "twelve_data"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.twelvedata.com",
        request_timeout_sec: float = 15,
        max_retries: int = 3,
        credit_budget_per_minute: int = 8,
        http_get: TwelveDataHttpGet | None = None,
        sleep_fn: SleepFn = time.sleep,
    ) -> None:
        if not api_key:
            raise TwelveDataProviderError("Twelve Data API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.request_timeout_sec = request_timeout_sec
        self.max_retries = max_retries
        self.credit_budget_per_minute = credit_budget_per_minute
        self.http_get = http_get or _urllib_get_json
        self.sleep_fn = sleep_fn
        self.request_count = 0
        self.retry_count = 0
        self.last_error: str | None = None
        self.last_success_at: datetime | None = None

    @classmethod
    def from_env(
        cls,
        *,
        api_key_env: str,
        base_url: str = "https://api.twelvedata.com",
        request_timeout_sec: float = 15,
        max_retries: int = 3,
        credit_budget_per_minute: int = 8,
        http_get: TwelveDataHttpGet | None = None,
        sleep_fn: SleepFn = time.sleep,
    ) -> "TwelveDataProvider":
        api_key = os.getenv(api_key_env)
        if not api_key:
            raise TwelveDataProviderError(f"missing API key env {api_key_env}")
        return cls(
            api_key=api_key,
            base_url=base_url,
            request_timeout_sec=request_timeout_sec,
            max_retries=max_retries,
            credit_budget_per_minute=credit_budget_per_minute,
            http_get=http_get,
            sleep_fn=sleep_fn,
        )

    def fetch_intraday_bars(
        self,
        symbols: list[str] | None = None,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        requested_symbols = [symbol.upper() for symbol in (symbols or [])]
        if not requested_symbols:
            raise TwelveDataProviderError("Twelve Data requires at least one symbol")
        if len(requested_symbols) > self.credit_budget_per_minute:
            raise TwelveDataProviderError(
                "Twelve Data request credit budget exceeded: "
                f"{len(requested_symbols)} symbols > {self.credit_budget_per_minute}"
            )

        standard_interval, provider_interval = _interval_pair(interval or "1m")
        outputsize = max(30, min(5000, _output_size(start, end, standard_interval)))
        bars: list[Bar] = []
        for symbol in requested_symbols:
            payload = self._request(
                {
                    "symbol": symbol,
                    "interval": provider_interval,
                    "outputsize": str(outputsize),
                    "timezone": "UTC",
                    "format": "JSON",
                    "apikey": self.api_key,
                }
            )
            bars.extend(
                _parse_twelve_data_bars(
                    payload,
                    symbol=symbol,
                    standard_interval=standard_interval,
                )
            )

        deduplicated = {bar.bar_id: bar for bar in bars}
        self.last_success_at = datetime.now(UTC)
        self.last_error = None
        return [
            bar
            for bar in sorted(deduplicated.values(), key=lambda item: (item.symbol, item.timestamp))
            if _matches_filters(bar, start=start, end=end)
        ]

    def fetch_provider_health(self) -> dict[str, str | int | float]:
        return redact_sensitive(
            {
                "provider": self.source,
                "status": "healthy" if self.last_error is None else "degraded",
                "requests": self.request_count,
                "retries": self.retry_count,
                "credit_budget_per_minute": self.credit_budget_per_minute,
                "last_success_at": self.last_success_at.isoformat() if self.last_success_at else "",
                "last_error": self.last_error or "",
            }
        )

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        endpoint = f"{self.base_url}/time_series"
        for attempt in range(self.max_retries + 1):
            self.request_count += 1
            try:
                payload = self.http_get(endpoint, params, self.request_timeout_sec)
                _raise_for_provider_error(payload)
                return payload
            except Exception as exc:
                error = exc if isinstance(exc, TwelveDataProviderError) else TwelveDataProviderError(str(exc))
                self.last_error = str(error)
                if attempt >= self.max_retries or not _is_retryable(error):
                    raise error
                self.retry_count += 1
                self.sleep_fn(min(8.0, 0.5 * (2**attempt)))
        raise TwelveDataProviderError("Twelve Data request exhausted retries")


def create_twelve_data_provider(
    *,
    api_key_env: str,
    base_url: str,
    request_timeout_sec: float,
    max_retries: int,
    credit_budget_per_minute: int,
    http_get: TwelveDataHttpGet | None = None,
    sleep_fn: SleepFn = time.sleep,
) -> TwelveDataProvider:
    return TwelveDataProvider.from_env(
        api_key_env=api_key_env,
        base_url=base_url,
        request_timeout_sec=request_timeout_sec,
        max_retries=max_retries,
        credit_budget_per_minute=credit_budget_per_minute,
        http_get=http_get,
        sleep_fn=sleep_fn,
    )


def _parse_twelve_data_bars(
    payload: dict[str, Any],
    *,
    symbol: str,
    standard_interval: str,
) -> list[Bar]:
    values = payload.get("values")
    if values is None:
        raise TwelveDataProviderError("Twelve Data response missing values")
    if not isinstance(values, list):
        raise TwelveDataProviderError("Twelve Data values must be a list")

    bars: list[Bar] = []
    for values_row in values:
        if not isinstance(values_row, dict):
            raise TwelveDataProviderError(f"Twelve Data invalid bar payload for {symbol}")
        try:
            timestamp = _parse_utc_timestamp(str(values_row["datetime"]))
            timestamp_text = timestamp.isoformat().replace("+00:00", "Z")
            bars.append(
                Bar(
                    bar_id=generate_bar_id(
                        symbol,
                        standard_interval,
                        timestamp_text,
                        TwelveDataProvider.source,
                    ),
                    symbol=symbol,
                    timestamp=timestamp,
                    interval=standard_interval,
                    open=float(values_row["open"]),
                    high=float(values_row["high"]),
                    low=float(values_row["low"]),
                    close=float(values_row["close"]),
                    volume=int(float(values_row.get("volume") or 0)),
                    vwap=None,
                    source=TwelveDataProvider.source,
                    quality_flag="normal",
                )
            )
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise TwelveDataProviderError(
                f"Twelve Data invalid OHLCV payload for {symbol}: {exc}"
            ) from exc
    return bars


def _raise_for_provider_error(payload: dict[str, Any]) -> None:
    status = str(payload.get("status", "")).lower()
    if status == "error" or payload.get("code"):
        code = payload.get("code", "unknown")
        message = payload.get("message") or payload.get("detail") or "provider error"
        raise TwelveDataProviderError(f"Twelve Data provider error {code}: {message}")


def _is_retryable(exc: TwelveDataProviderError) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("429", "rate", "limit", "quota", "timeout", "temporar", "connection", "503")
    )


def _parse_utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _interval_pair(interval: str) -> tuple[str, str]:
    mapping = {
        "1m": ("1m", "1min"),
        "5m": ("5m", "5min"),
        "15m": ("15m", "15min"),
        "30m": ("30m", "30min"),
        "1min": ("1m", "1min"),
        "5min": ("5m", "5min"),
        "15min": ("15m", "15min"),
        "30min": ("30m", "30min"),
    }
    try:
        return mapping[interval]
    except KeyError as exc:
        raise TwelveDataProviderError(f"unsupported Twelve Data interval: {interval}") from exc


def _output_size(start: datetime | None, end: datetime | None, interval: str) -> int:
    if start is None or end is None:
        return 500
    minutes = max(1, int((end - start).total_seconds() // 60) + 1)
    interval_minutes = int(interval.removesuffix("m"))
    return max(1, minutes // interval_minutes + 2)


def _matches_filters(
    bar: Bar,
    *,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if start is not None and bar.timestamp < start:
        return False
    if end is not None and bar.timestamp > end:
        return False
    return True


def _urllib_get_json(url: str, params: dict[str, str], timeout: float) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "stock-agent/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


__all__ = [
    "TwelveDataProvider",
    "TwelveDataProviderError",
    "create_twelve_data_provider",
]
