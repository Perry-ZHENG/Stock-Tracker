"""Cross-provider bar comparison for supervisor data-quality checks."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from stock_agent.health import HealthThresholds, record_health_metric
from stock_agent.schemas import Bar, TraceChain
from stock_agent.storage.repositories import insert_trace_chain
from stock_agent.tracing import create_trace

CompareStatus = Literal["ok", "skipped", "degraded", "unhealthy"]


@dataclass(frozen=True)
class ProviderCompareThresholds:
    price_diff_bps: float = 50.0
    volume_diff_ratio: float = 0.25
    max_timestamp_skew_sec: float = 60.0


@dataclass(frozen=True)
class ProviderCompareIssue:
    symbol: str
    primary_bar_id: str | None
    secondary_bar_id: str | None
    issue_type: str
    severity: CompareStatus
    detail: str


@dataclass(frozen=True)
class ProviderCompareResult:
    status: CompareStatus
    compared: int
    skipped: bool = False
    issues: list[ProviderCompareIssue] = field(default_factory=list)

    @property
    def should_suppress_signals(self) -> bool:
        return self.status in {"degraded", "unhealthy"}


def compare_provider_bars(
    *,
    primary_bars: list[Bar],
    secondary_bars: list[Bar] | None,
    thresholds: ProviderCompareThresholds | None = None,
) -> ProviderCompareResult:
    active_thresholds = thresholds or ProviderCompareThresholds()
    if not secondary_bars:
        return ProviderCompareResult(status="skipped", compared=0, skipped=True)

    secondary_by_key = {(bar.symbol, bar.timestamp, bar.interval): bar for bar in secondary_bars}
    issues: list[ProviderCompareIssue] = []
    compared = 0
    for primary in primary_bars:
        secondary = secondary_by_key.get((primary.symbol, primary.timestamp, primary.interval))
        if secondary is None:
            issues.append(
                ProviderCompareIssue(
                    symbol=primary.symbol,
                    primary_bar_id=primary.bar_id,
                    secondary_bar_id=None,
                    issue_type="missing_secondary_bar",
                    severity="degraded",
                    detail=f"no secondary bar for {primary.symbol} at {primary.timestamp.isoformat()}",
                )
            )
            continue
        compared += 1
        issues.extend(_compare_pair(primary, secondary, active_thresholds))

    status: CompareStatus = "ok"
    if any(issue.severity == "unhealthy" for issue in issues):
        status = "unhealthy"
    elif issues:
        status = "degraded"
    return ProviderCompareResult(status=status, compared=compared, issues=issues)


def apply_compare_quality(bars: list[Bar], result: ProviderCompareResult) -> list[Bar]:
    if not result.should_suppress_signals:
        return bars
    affected = {issue.primary_bar_id for issue in result.issues if issue.primary_bar_id}
    return [
        bar.model_copy(update={"quality_flag": _append_quality(bar.quality_flag, f"provider_compare_{result.status}")})
        if bar.bar_id in affected
        else bar
        for bar in bars
    ]


def persist_provider_compare(
    connection: sqlite3.Connection,
    result: ProviderCompareResult,
    *,
    primary_provider: str,
    secondary_provider: str | None,
    thresholds: ProviderCompareThresholds | None = None,
) -> TraceChain:
    trace = _trace_for_result(
        result,
        primary_provider=primary_provider,
        secondary_provider=secondary_provider,
        thresholds=thresholds or ProviderCompareThresholds(),
    )
    insert_trace_chain(connection, trace)
    record_health_metric(
        connection,
        module="provider_compare",
        data_latency_sec=0,
        error_rate=0 if result.status in {"ok", "skipped"} else 1,
        consecutive_failures=0 if result.status in {"ok", "skipped"} else 1,
        alert_failures=len(result.issues),
        details={
            "status": result.status,
            "compared": result.compared,
            "skipped": result.skipped,
            "primary_provider": primary_provider,
            "secondary_provider": secondary_provider or "none",
            "issues": [_issue_payload(issue) for issue in result.issues],
        },
        thresholds=HealthThresholds(),
    )
    return trace


def _compare_pair(primary: Bar, secondary: Bar, thresholds: ProviderCompareThresholds) -> list[ProviderCompareIssue]:
    issues: list[ProviderCompareIssue] = []
    price_diff_bps = abs(primary.close - secondary.close) / primary.close * 10000 if primary.close else 0
    if price_diff_bps > thresholds.price_diff_bps:
        issues.append(
            ProviderCompareIssue(
                symbol=primary.symbol,
                primary_bar_id=primary.bar_id,
                secondary_bar_id=secondary.bar_id,
                issue_type="close_diff_bps",
                severity="unhealthy",
                detail=f"close diff {price_diff_bps:.2f}bps exceeds {thresholds.price_diff_bps:.2f}bps",
            )
        )
    volume_diff_ratio = abs(primary.volume - secondary.volume) / max(primary.volume, 1)
    if volume_diff_ratio > thresholds.volume_diff_ratio:
        issues.append(
            ProviderCompareIssue(
                symbol=primary.symbol,
                primary_bar_id=primary.bar_id,
                secondary_bar_id=secondary.bar_id,
                issue_type="volume_diff_ratio",
                severity="degraded",
                detail=f"volume diff {volume_diff_ratio:.4f} exceeds {thresholds.volume_diff_ratio:.4f}",
            )
        )
    skew = abs((primary.timestamp - secondary.timestamp).total_seconds())
    if skew > thresholds.max_timestamp_skew_sec:
        issues.append(
            ProviderCompareIssue(
                symbol=primary.symbol,
                primary_bar_id=primary.bar_id,
                secondary_bar_id=secondary.bar_id,
                issue_type="timestamp_skew_sec",
                severity="degraded",
                detail=f"timestamp skew {skew:.2f}s exceeds {thresholds.max_timestamp_skew_sec:.2f}s",
            )
        )
    return issues


def _trace_for_result(
    result: ProviderCompareResult,
    *,
    primary_provider: str,
    secondary_provider: str | None,
    thresholds: ProviderCompareThresholds,
) -> TraceChain:
    payload = f"{primary_provider}|{secondary_provider}|{result.status}|{result.compared}|{len(result.issues)}"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return create_trace(
        trace_id=f"trace-provider-compare-{digest}",
        module="provider_compare",
        input_ref={
            "primary_provider": primary_provider,
            "secondary_provider": secondary_provider or "none",
            "thresholds": thresholds.__dict__,
        },
        output_ref={
            "status": result.status,
            "compared": result.compared,
            "skipped": result.skipped,
            "issues": [_issue_payload(issue) for issue in result.issues],
        },
        status="success" if result.status in {"ok", "skipped"} else "failed",
        error_msg=None if result.status in {"ok", "skipped"} else f"provider compare {result.status}",
    )


def _issue_payload(issue: ProviderCompareIssue) -> dict[str, str | None]:
    return {
        "symbol": issue.symbol,
        "primary_bar_id": issue.primary_bar_id,
        "secondary_bar_id": issue.secondary_bar_id,
        "issue_type": issue.issue_type,
        "severity": issue.severity,
        "detail": issue.detail,
    }


def _append_quality(current: str, flag: str) -> str:
    parts = [part for part in current.split("|") if part and part != "normal"]
    if flag not in parts:
        parts.append(flag)
    return "|".join(parts) if parts else "normal"


__all__ = [
    "ProviderCompareIssue",
    "ProviderCompareResult",
    "ProviderCompareThresholds",
    "apply_compare_quality",
    "compare_provider_bars",
    "persist_provider_compare",
]
