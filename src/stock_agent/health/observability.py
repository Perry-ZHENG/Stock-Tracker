"""Verbose health and observability summaries."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from stock_agent.schemas import HealthMetric, TraceChain
from stock_agent.security import redact_sensitive
from stock_agent.storage.repositories import (
    list_abnormal_bars,
    list_config_changes,
    list_health_metrics,
    list_notifications,
    list_trace_chain,
)

OBSERVED_MODULES = (
    "provider_registry",
    "provider_compare",
    "bar_builder",
    "strategy",
    "supervisor",
    "notification",
    "worker",
)


@dataclass(frozen=True)
class ObservabilitySummary:
    module_status: dict[str, str]
    provider_success_rate: float | None
    provider_fallback_count: int
    abnormal_bar_count: int
    supervisor_intercept_count: int
    notification_pending: int
    notification_failed: int
    config_review_backlog: int
    recent_failed_traces: list[TraceChain] = field(default_factory=list)
    recent_provider_fallbacks: list[TraceChain] = field(default_factory=list)
    recent_errors: list[str] = field(default_factory=list)


def build_observability_summary(connection: sqlite3.Connection, *, limit: int = 20) -> ObservabilitySummary:
    metrics = list_health_metrics(connection, limit=200)
    traces = list_trace_chain(connection, limit=200)
    notifications = list_notifications(connection, limit=200)
    config_changes = list_config_changes(connection, limit=200)
    abnormal_bars = list_abnormal_bars(connection, limit=200)

    module_status = _latest_module_status(metrics)
    provider_metrics = [metric for metric in metrics if metric.module == "provider_registry"]
    provider_success_rate = _success_rate(provider_metrics)
    provider_fallbacks = [
        trace for trace in traces
        if trace.module in {"provider_registry", "provider_compare"}
        and (_trace_mentions_fallback(trace) or trace.status == "failed")
    ][:limit]
    failed_traces = [trace for trace in traces if trace.status == "failed"][:limit]
    pending_notifications = [row for row in notifications if row["status"] in {"pending", "sending"}]
    failed_notifications = [row for row in notifications if row["status"] in {"failed", "suppressed"}]
    pending_reviews = [row for row in config_changes if row["status"] in {"draft", "pending_review"}]
    supervisor_intercepts = [
        metric for metric in metrics
        if metric.module == "supervisor" and (metric.alert_failures > 0 or metric.status in {"degraded", "unhealthy"})
    ]

    return ObservabilitySummary(
        module_status=module_status,
        provider_success_rate=provider_success_rate,
        provider_fallback_count=len(provider_fallbacks),
        abnormal_bar_count=len([row for row in abnormal_bars if row["status"] == "quarantined"]),
        supervisor_intercept_count=len(supervisor_intercepts),
        notification_pending=len(pending_notifications),
        notification_failed=len(failed_notifications),
        config_review_backlog=len(pending_reviews),
        recent_failed_traces=failed_traces,
        recent_provider_fallbacks=provider_fallbacks,
        recent_errors=_recent_errors(metrics, traces, notifications, limit=limit),
    )


def format_observability_summary(summary: ObservabilitySummary) -> str:
    lines = ["verbose_health_status=ok", "module | status"]
    for module in OBSERVED_MODULES:
        lines.append(f"{module} | {summary.module_status.get(module, 'unknown')}")
    lines.extend(
        [
            f"provider_success_rate={_format_optional_float(summary.provider_success_rate)}",
            f"provider_fallback_count={summary.provider_fallback_count}",
            f"abnormal_bar_count={summary.abnormal_bar_count}",
            f"supervisor_intercept_count={summary.supervisor_intercept_count}",
            f"notification_pending={summary.notification_pending}",
            f"notification_failed={summary.notification_failed}",
            f"config_review_backlog={summary.config_review_backlog}",
            f"recent_failed_traces={len(summary.recent_failed_traces)}",
            f"recent_provider_fallbacks={len(summary.recent_provider_fallbacks)}",
        ]
    )
    if summary.recent_errors:
        lines.append("recent_errors:")
        for error in summary.recent_errors[:10]:
            lines.append(f"- {error}")
    return "\n".join(lines) + "\n"


def _latest_module_status(metrics: list[HealthMetric]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for metric in metrics:
        statuses.setdefault(metric.module, metric.status)
    return {module: statuses.get(module, "unknown") for module in OBSERVED_MODULES}


def _success_rate(metrics: list[HealthMetric]) -> float | None:
    if not metrics:
        return None
    successes = sum(1 for metric in metrics if metric.status == "healthy")
    return successes / len(metrics)


def _trace_mentions_fallback(trace: TraceChain) -> bool:
    ref = trace.output_ref if isinstance(trace.output_ref, dict) else {}
    return "fallback" in str(redact_sensitive(ref)).lower()


def _recent_errors(metrics: list[HealthMetric], traces: list[TraceChain], notifications: list[dict[str, Any]], *, limit: int) -> list[str]:
    errors: list[str] = []
    for trace in traces:
        if trace.status == "failed" and trace.error_msg:
            errors.append(f"trace:{trace.trace_id}:{trace.error_msg}")
    for metric in metrics:
        if metric.status in {"degraded", "unhealthy"}:
            errors.append(f"health:{metric.module}:{redact_sensitive(metric.details)}")
    for notification in notifications:
        if notification.get("error_msg"):
            errors.append(f"notification:{notification['notification_id']}:{notification['error_msg']}")
    return [str(redact_sensitive(error)) for error in errors[:limit]]


def _format_optional_float(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.4f}"


__all__ = ["OBSERVED_MODULES", "ObservabilitySummary", "build_observability_summary", "format_observability_summary"]
