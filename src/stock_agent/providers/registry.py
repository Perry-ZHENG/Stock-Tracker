"""Provider selection, fallback, and audit for market data."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from stock_agent.config import StockAgentConfig
from stock_agent.health import HealthThresholds, record_health_metric
from stock_agent.providers.base import MarketDataProvider
from stock_agent.providers.broker_market_data import BrokerMarketDataProviderError, create_broker_market_data_provider
from stock_agent.providers.csv_demo import CsvDemoProvider, CsvDemoProviderError
from stock_agent.providers.live import LiveProviderError, create_live_provider
from stock_agent.schemas import Bar
from stock_agent.storage.repositories import insert_notification, insert_trace_chain
from stock_agent.tracing import create_trace, utc_now

ProviderErrorType = Literal["configuration", "rate_limit", "latency", "data", "unknown"]
ProviderStatus = Literal["success", "failed"]
ProviderFactory = Callable[[], MarketDataProvider]


class ProviderRegistryError(RuntimeError):
    """Raised when no configured market data provider can return bars."""


@dataclass(frozen=True)
class ProviderAttempt:
    provider_name: str
    status: ProviderStatus
    latency_sec: float
    request_id: str
    error_type: ProviderErrorType | None = None
    error_msg: str | None = None
    bar_count: int = 0


@dataclass(frozen=True)
class ProviderFetchResult:
    provider_name: str
    bars: list[Bar]
    attempts: list[ProviderAttempt]
    fallback_used: bool
    request_id: str
    provider_health: dict[str, str | int | float]
    latency_sec: float


class ProviderRegistry:
    """Choose configured providers and fall back without leaking vendor shapes."""

    def __init__(
        self,
        *,
        root: Path,
        config: StockAgentConfig,
        connection: sqlite3.Connection | None = None,
        provider_factories: dict[str, ProviderFactory] | None = None,
    ) -> None:
        self.root = root
        self.config = config
        self.connection = connection
        self.provider_factories = provider_factories or {}

    def fetch_intraday_bars(
        self,
        *,
        symbols: list[str] | None = None,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> ProviderFetchResult:
        attempts: list[ProviderAttempt] = []
        for provider_name in self._provider_order():
            request_id = _request_id(provider_name)
            started = time.perf_counter()
            try:
                provider = self._create_provider(provider_name)
                bars = provider.fetch_intraday_bars(
                    symbols=symbols,
                    interval=interval,
                    start=start,
                    end=end,
                )
                latency_sec = time.perf_counter() - started
                attempt = ProviderAttempt(
                    provider_name=provider_name,
                    status="success",
                    latency_sec=latency_sec,
                    request_id=request_id,
                    bar_count=len(bars),
                )
                attempts.append(attempt)
                result = ProviderFetchResult(
                    provider_name=provider_name,
                    bars=bars,
                    attempts=attempts,
                    fallback_used=len(attempts) > 1,
                    request_id=request_id,
                    provider_health=provider.fetch_provider_health(),
                    latency_sec=latency_sec,
                )
                self._record_success(result)
                return result
            except Exception as exc:
                latency_sec = time.perf_counter() - started
                attempts.append(
                    ProviderAttempt(
                        provider_name=provider_name,
                        status="failed",
                        latency_sec=latency_sec,
                        request_id=request_id,
                        error_type=_classify_provider_error(exc),
                        error_msg=str(exc),
                    )
                )

        self._record_failure(attempts)
        message = "all configured market data providers failed"
        if attempts:
            message += f": {attempts[-1].provider_name}: {attempts[-1].error_msg}"
        raise ProviderRegistryError(message)

    def _provider_order(self) -> list[str]:
        ordered: list[str] = []
        for provider_name in [*self.config.provider.priority, self.config.provider.default]:
            _append_unique(ordered, provider_name)
        if self.config.provider.fallback.enabled:
            for provider_name in self.config.provider.fallback.order:
                _append_unique(ordered, provider_name)
        return ordered

    def _create_provider(self, provider_name: str) -> MarketDataProvider:
        normalized = provider_name.lower()
        if provider_name in self.provider_factories:
            return self.provider_factories[provider_name]()
        if normalized in self.provider_factories:
            return self.provider_factories[normalized]()
        if normalized == "csv_demo":
            return CsvDemoProvider(self.root / self.config.provider.csv_demo.path)
        if normalized in {"live", self.config.provider.live.name.lower(), "alpha_vantage"}:
            return create_live_provider(
                provider_name=self.config.provider.live.name,
                api_key_env=self.config.provider.live.api_key_env,
            )
        if normalized in {"broker", "broker_market_data"}:
            return create_broker_market_data_provider()
        if normalized in {"cache", "fallback"}:
            raise ProviderRegistryError("cache fallback provider is not implemented yet")
        raise ProviderRegistryError(f"unsupported provider: {provider_name}")

    def _record_success(self, result: ProviderFetchResult) -> None:
        if self.connection is None:
            return
        if result.fallback_used:
            insert_trace_chain(self.connection, _trace_for_attempts(result.attempts, status="success"))
            _insert_provider_notification(
                self.connection,
                status="pending",
                provider_name=result.provider_name,
                attempts=result.attempts,
                message="provider fallback succeeded",
            )
        record_health_metric(
            self.connection,
            module="provider_registry",
            data_latency_sec=result.latency_sec,
            error_rate=_fallback_error_rate(self.config) if result.fallback_used else 0,
            consecutive_failures=0,
            details={
                "provider": result.provider_name,
                "fallback_used": result.fallback_used,
                "attempts": [_attempt_payload(attempt) for attempt in result.attempts],
                "request_id": result.request_id,
            },
            thresholds=HealthThresholds.from_config(self.config.health),
        )

    def _record_failure(self, attempts: list[ProviderAttempt]) -> None:
        if self.connection is None:
            return
        insert_trace_chain(self.connection, _trace_for_attempts(attempts, status="failed"))
        _insert_provider_notification(
            self.connection,
            status="pending",
            provider_name=None,
            attempts=attempts,
            message="all providers failed",
        )
        record_health_metric(
            self.connection,
            module="provider_registry",
            data_latency_sec=sum(attempt.latency_sec for attempt in attempts),
            error_rate=1,
            consecutive_failures=len(attempts),
            details={"attempts": [_attempt_payload(attempt) for attempt in attempts]},
            thresholds=HealthThresholds.from_config(self.config.health),
        )


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


def _classify_provider_error(exc: Exception) -> ProviderErrorType:
    message = str(exc).lower()
    if isinstance(exc, CsvDemoProviderError):
        return "data"
    if isinstance(exc, BrokerMarketDataProviderError):
        return "configuration"
    if isinstance(exc, LiveProviderError) and any(
        token in message for token in ("missing api key", "unsupported", "required")
    ):
        return "configuration"
    if any(token in message for token in ("rate", "throttle", "quota", "limit")):
        return "rate_limit"
    if any(token in message for token in ("timeout", "latency", "delay")):
        return "latency"
    if any(token in message for token in ("invalid", "missing columns", "file not found", "parse")):
        return "data"
    return "unknown"


def _fallback_error_rate(config: StockAgentConfig) -> float:
    return (config.health.error_rate_degraded + config.health.error_rate_unhealthy) / 2


def _trace_for_attempts(attempts: list[ProviderAttempt], *, status: str):
    error_msg = None
    failed = [attempt for attempt in attempts if attempt.status == "failed"]
    if failed:
        error_msg = "; ".join(
            f"{attempt.provider_name}({attempt.error_type}): {attempt.error_msg}" for attempt in failed
        )
    return create_trace(
        trace_id=_trace_id(attempts),
        module="provider_registry",
        input_ref={"providers": [attempt.provider_name for attempt in attempts]},
        output_ref={"attempts": [_attempt_payload(attempt) for attempt in attempts]},
        status=status,
        error_msg=error_msg,
    )


def _insert_provider_notification(
    connection: sqlite3.Connection,
    *,
    status: str,
    provider_name: str | None,
    attempts: list[ProviderAttempt],
    message: str,
) -> None:
    now = utc_now()
    payload = {
        "type": "provider_fallback",
        "message": message,
        "provider": provider_name,
        "attempts": [_attempt_payload(attempt) for attempt in attempts],
    }
    insert_notification(
        connection,
        notification_id=_notification_id(attempts, message),
        channel="provider_registry",
        status=status,
        payload=payload,
        retry_count=0,
        error_msg=None,
        created_at=now,
        updated_at=now,
    )


def _attempt_payload(attempt: ProviderAttempt) -> dict[str, object]:
    return {
        "provider_name": attempt.provider_name,
        "status": attempt.status,
        "latency_sec": attempt.latency_sec,
        "request_id": attempt.request_id,
        "error_type": attempt.error_type,
        "error_msg": attempt.error_msg,
        "bar_count": attempt.bar_count,
    }


def _request_id(provider_name: str) -> str:
    return f"req-{provider_name}-{uuid4().hex[:12]}"


def _trace_id(attempts: list[ProviderAttempt]) -> str:
    payload = "|".join(attempt.request_id for attempt in attempts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"trace-provider-{digest}"


def _notification_id(attempts: list[ProviderAttempt], message: str) -> str:
    payload = "|".join([message, *[attempt.request_id for attempt in attempts]])
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"notif-provider-{digest}"


__all__ = [
    "ProviderAttempt",
    "ProviderErrorType",
    "ProviderFetchResult",
    "ProviderRegistry",
    "ProviderRegistryError",
]
