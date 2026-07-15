
"""Standard data schemas for Stock Agent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from stock_agent.contracts.common import StrictSchema, ensure_utc

TraceStatus = Literal["success", "skipped", "failed"]


def _ensure_utc(value: datetime | None) -> datetime | None:
    """Compatibility wrapper for the V2 shared UTC validator."""
    return ensure_utc(value)


class Bar(StrictSchema):
    bar_id: str
    symbol: str
    timestamp: datetime
    interval: str = "30m"
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(ge=0)
    vwap: float | None = None
    source: str = "demo"
    quality_flag: str = "normal"

    @field_validator("timestamp")
    @classmethod
    def _timestamp_to_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)  # type: ignore[return-value]


class TraceChain(StrictSchema):
    """A durable audit trace for a provider, tool, model, or Agent action."""
    trace_id: str
    parent_id: str | None = None
    module: str
    input_ref: list[Any] | dict[str, Any] = Field(default_factory=list)
    output_ref: list[Any] | dict[str, Any] = Field(default_factory=list)
    status: TraceStatus = "success"
    error_msg: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _created_at_to_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)  # type: ignore[return-value]


class NewsItem(StrictSchema):
    news_id: str
    symbol: str | None = None
    market: str | None = "US"
    title: str
    summary: str
    url: str
    source: str
    published_at: datetime
    retention_level: str = "raw_summary"
    created_at: datetime

    @field_validator("published_at", "created_at")
    @classmethod
    def _datetime_to_utc(cls, value: datetime) -> datetime:
        return _ensure_utc(value)  # type: ignore[return-value]


class HealthMetric(StrictSchema):
    metric_id: str
    timestamp: datetime
    module: str
    heartbeat_at: datetime | None = None
    data_latency_sec: float = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    consecutive_failures: int = Field(ge=0)
    alert_failures: int = Field(ge=0)
    status: Literal["healthy", "degraded", "unhealthy"] = "healthy"
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", "heartbeat_at")
    @classmethod
    def _datetime_to_utc(cls, value: datetime | None) -> datetime | None:
        return _ensure_utc(value)


__all__ = [
    "Bar",
    "TraceChain",
    "NewsItem",
    "HealthMetric",
]
