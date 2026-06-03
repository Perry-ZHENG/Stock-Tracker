"""Health CLI command."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.schemas import HealthMetric, TraceChain
from stock_agent.storage.repositories import list_health_metrics, list_trace_chain
from stock_agent.storage.sqlite import open_database


@dataclass(frozen=True)
class HealthCommandResult:
    status: str
    metric: HealthMetric | None
    recent_failed_traces: list[TraceChain]
    sqlite_path: Path


def run_health(root: Path, *, stream: TextIO | None = None) -> HealthCommandResult:
    output = stream or sys.stdout
    config = validate_config(DEFAULT_CONFIG)
    sqlite_path = root / config.storage.sqlite_path

    if not sqlite_path.exists():
        output.write(f"health_status=unhealthy\nsqlite_path={sqlite_path}\nerror=no runtime database\n")
        output.flush()
        return HealthCommandResult(
            status="unhealthy",
            metric=None,
            recent_failed_traces=[],
            sqlite_path=sqlite_path,
        )

    connection = open_database(sqlite_path)
    metrics = list_health_metrics(connection, limit=1)
    failed_traces = [
        trace for trace in list_trace_chain(connection, limit=10) if trace.status == "failed"
    ]

    if not metrics:
        output.write(f"health_status=unhealthy\nsqlite_path={sqlite_path}\nerror=no health metrics\n")
        output.flush()
        return HealthCommandResult(
            status="unhealthy",
            metric=None,
            recent_failed_traces=failed_traces,
            sqlite_path=sqlite_path,
        )

    metric = metrics[0]
    output.write(f"health_status={metric.status}\n")
    output.write(f"module={metric.module}\n")
    output.write(f"timestamp={metric.timestamp.isoformat().replace('+00:00', 'Z')}\n")
    heartbeat = metric.heartbeat_at.isoformat().replace("+00:00", "Z") if metric.heartbeat_at else "none"
    output.write(f"heartbeat_at={heartbeat}\n")
    output.write(f"data_latency_sec={metric.data_latency_sec}\n")
    output.write(f"error_rate={metric.error_rate}\n")
    output.write(f"consecutive_failures={metric.consecutive_failures}\n")
    output.write(f"alert_failures={metric.alert_failures}\n")
    output.write(f"recent_failed_traces={len(failed_traces)}\n")
    output.write(f"sqlite_path={sqlite_path}\n")
    output.flush()
    return HealthCommandResult(
        status=metric.status,
        metric=metric,
        recent_failed_traces=failed_traces,
        sqlite_path=sqlite_path,
    )


__all__ = ["HealthCommandResult", "run_health"]
