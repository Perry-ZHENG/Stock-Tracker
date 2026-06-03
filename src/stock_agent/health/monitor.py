"""Health metric classification and persistence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from stock_agent.config import HealthConfig
from stock_agent.schemas import HealthMetric
from stock_agent.storage.repositories import insert_health_metric
from stock_agent.tracing import utc_now

HealthStatus = Literal["healthy", "degraded", "unhealthy"]


@dataclass(frozen=True)
class HealthThresholds:
    heartbeat_timeout_sec: int = 300
    data_delay_degraded_sec: int = 60
    data_delay_unhealthy_sec: int = 300
    error_rate_degraded: float = 0.01
    error_rate_unhealthy: float = 0.05
    consecutive_failure_unhealthy: int = 3

    @classmethod
    def from_config(cls, config: HealthConfig) -> "HealthThresholds":
        return cls(
            heartbeat_timeout_sec=config.heartbeat_timeout_sec,
            data_delay_degraded_sec=config.data_delay_degraded_sec,
            data_delay_unhealthy_sec=config.data_delay_unhealthy_sec,
            error_rate_degraded=config.error_rate_degraded,
            error_rate_unhealthy=config.error_rate_unhealthy,
            consecutive_failure_unhealthy=config.consecutive_failure_unhealthy,
        )


def classify_health_status(
    *,
    now: datetime,
    heartbeat_at: datetime | None,
    data_latency_sec: float,
    error_rate: float,
    consecutive_failures: int,
    core_module_running: bool = True,
    thresholds: HealthThresholds | None = None,
) -> HealthStatus:
    active_thresholds = thresholds or HealthThresholds()
    heartbeat_age_sec = None
    if heartbeat_at is not None:
        heartbeat_age_sec = max(0.0, (now - heartbeat_at).total_seconds())

    if (
        not core_module_running
        or heartbeat_at is None
        or heartbeat_age_sec is None
        or heartbeat_age_sec > active_thresholds.heartbeat_timeout_sec
        or data_latency_sec > active_thresholds.data_delay_unhealthy_sec
        or error_rate >= active_thresholds.error_rate_unhealthy
        or consecutive_failures >= active_thresholds.consecutive_failure_unhealthy
    ):
        return "unhealthy"

    if (
        data_latency_sec >= active_thresholds.data_delay_degraded_sec
        or error_rate >= active_thresholds.error_rate_degraded
        or consecutive_failures > 0
    ):
        return "degraded"

    return "healthy"


def build_health_metric(
    *,
    module: str,
    heartbeat_at: datetime | None = None,
    data_latency_sec: float = 0,
    error_rate: float = 0,
    consecutive_failures: int = 0,
    alert_failures: int = 0,
    core_module_running: bool = True,
    details: dict[str, object] | None = None,
    now: datetime | None = None,
    thresholds: HealthThresholds | None = None,
) -> HealthMetric:
    timestamp = now or utc_now()
    heartbeat = heartbeat_at or timestamp
    status = classify_health_status(
        now=timestamp,
        heartbeat_at=heartbeat,
        data_latency_sec=data_latency_sec,
        error_rate=error_rate,
        consecutive_failures=consecutive_failures,
        core_module_running=core_module_running,
        thresholds=thresholds,
    )
    return HealthMetric(
        metric_id=_metric_id(module, timestamp),
        timestamp=timestamp,
        module=module,
        heartbeat_at=heartbeat,
        data_latency_sec=data_latency_sec,
        error_rate=error_rate,
        consecutive_failures=consecutive_failures,
        alert_failures=alert_failures,
        status=status,
        details=details or {},
    )


def record_health_metric(
    connection: sqlite3.Connection,
    *,
    module: str,
    heartbeat_at: datetime | None = None,
    data_latency_sec: float = 0,
    error_rate: float = 0,
    consecutive_failures: int = 0,
    alert_failures: int = 0,
    core_module_running: bool = True,
    details: dict[str, object] | None = None,
    now: datetime | None = None,
    thresholds: HealthThresholds | None = None,
) -> HealthMetric:
    metric = build_health_metric(
        module=module,
        heartbeat_at=heartbeat_at,
        data_latency_sec=data_latency_sec,
        error_rate=error_rate,
        consecutive_failures=consecutive_failures,
        alert_failures=alert_failures,
        core_module_running=core_module_running,
        details=details,
        now=now,
        thresholds=thresholds,
    )
    insert_health_metric(connection, metric)
    return metric


def _metric_id(module: str, timestamp: datetime) -> str:
    payload = f"{module}|{timestamp.isoformat()}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"health-{module}-{digest}"
