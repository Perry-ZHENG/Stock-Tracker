"""Market-watch worker pipeline integration."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO
from zoneinfo import ZoneInfo

from stock_agent.bars import BarBuilder, update_bar_checkpoint
from stock_agent.bars.quarantine import persist_quarantine_result, quarantine_abnormal_bars
from stock_agent.config import StockAgentConfig
from stock_agent.health import HealthThresholds, record_health_metric
from stock_agent.notifications import (
    CliNotificationSink,
    NotificationOutbox,
    persist_approved_signals,
)
from stock_agent.providers import ProviderRegistry
from stock_agent.scheduler import build_watch_schedule
from stock_agent.signals import SignalPipeline
from stock_agent.storage import LakeWriter
from stock_agent.storage.repositories import insert_trace_chain
from stock_agent.supervisor.provider_compare import apply_compare_quality, compare_provider_bars, persist_provider_compare
from stock_agent.supervisor import supervise_candidate_signals
from stock_agent.tracing import utc_now
from stock_agent.worker.identity import WorkerIdentity, build_worker_identity


@dataclass(frozen=True)
class WorkerTickSummary:
    status: str
    trading_day: bool
    provider: str | None = None
    raw_bars: int = 0
    prepared_bars: int = 0
    candidate_signals: int = 0
    approved_signals: int = 0
    rejected_signals: int = 0
    notifications: int = 0
    lake_writes: int = 0
    trace_count: int = 0
    errors: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        values = {
            "tick_status": self.status,
            "trading_day": str(self.trading_day).lower(),
            "provider": self.provider or "none",
            "raw_bars": self.raw_bars,
            "prepared_bars": self.prepared_bars,
            "candidate_signals": self.candidate_signals,
            "approved_signals": self.approved_signals,
            "rejected_signals": self.rejected_signals,
            "notifications": self.notifications,
            "lake_writes": self.lake_writes,
            "trace_count": self.trace_count,
            "errors": len(self.errors),
        }
        return [f"{key}={value}" for key, value in values.items()]


class WorkerPipeline:
    """One market-watch tick from provider fetch through notification and health."""

    def __init__(
        self,
        *,
        root: Path,
        config: StockAgentConfig,
        connection: sqlite3.Connection,
        notification_stream: TextIO | None = None,
        now_fn: Callable[[], datetime] = utc_now,
        identity: WorkerIdentity | None = None,
    ) -> None:
        self.root = root
        self.config = config
        self.connection = connection
        self.notification_stream = notification_stream
        self.now_fn = now_fn
        self.identity = identity or build_worker_identity()
        self.thresholds = HealthThresholds.from_config(config.health)

    def run_once(self) -> WorkerTickSummary:
        now = self.now_fn()
        schedule = build_watch_schedule(
            config=self.config.schedule,
            target_date=now.astimezone(ZoneInfo(self.config.schedule.timezone)).date(),
        )
        if not schedule.market_day.is_trading_day:
            summary = WorkerTickSummary(status="market_closed", trading_day=False)
            self._record_worker_health(summary, details={"market_note": schedule.market_day.holiday_name})
            return summary

        registry = ProviderRegistry(root=self.root, config=self.config, connection=self.connection)
        provider_result = registry.fetch_intraday_bars(
            symbols=self.config.symbols.default,
            interval=self.config.bar.interval,
        )
        raw_bars = provider_result.bars
        compare_result = compare_provider_bars(primary_bars=raw_bars, secondary_bars=None)
        persist_provider_compare(
            self.connection,
            compare_result,
            primary_provider=provider_result.provider_name,
            secondary_provider=None,
        )
        raw_bars = apply_compare_quality(raw_bars, compare_result)
        quarantine_result = quarantine_abnormal_bars(raw_bars)
        persist_quarantine_result(self.connection, quarantine_result)
        raw_bars = quarantine_result.clean_bars
        prepared_bars = BarBuilder(
            regular_session_only=self.config.bar.session == "regular_only"
        ).from_standard_bars(raw_bars)

        lake_writes = 0
        if raw_bars:
            LakeWriter(self.root / self.config.storage.parquet_root).write_raw_bars(raw_bars)
            lake_writes += 1
        for bar in prepared_bars:
            update_bar_checkpoint(
                self.connection,
                symbol=bar.symbol,
                interval=bar.interval,
                window_end=bar.timestamp,
            )

        signal_result = SignalPipeline(config=self.config, connection=self.connection).run(prepared_bars)
        if compare_result.should_suppress_signals:
            supervisor_result = supervise_candidate_signals(
                bars=prepared_bars,
                candidate_signals=[],
                traces=signal_result.traces,
                strategy_params=signal_result.snapshot.strategy_params,
                connection=self.connection,
            )
        else:
            supervisor_result = supervise_candidate_signals(
                bars=prepared_bars,
                candidate_signals=signal_result.signals,
                traces=signal_result.traces,
                strategy_params=signal_result.snapshot.strategy_params,
                connection=self.connection,
            )
        for trace in supervisor_result.traces:
            insert_trace_chain(self.connection, trace)

        persist_approved_signals(self.connection, supervisor_result.approved_signals)
        notification_count = 0
        if supervisor_result.approved_signals:
            channels = ["cli"] if self.notification_stream is not None else []
            outbox = NotificationOutbox(self.connection, instance_id=self.identity.instance_id)
            outbox.enqueue_signals(supervisor_result.approved_signals, channels=channels)
            if self.notification_stream is not None:
                dispatch_result = outbox.dispatch_pending(
                    {"cli": CliNotificationSink(self.notification_stream)},
                    max_retries=5,
                )
                notification_count = dispatch_result.sent

        summary = WorkerTickSummary(
            status="ok" if supervisor_result.ok else "supervisor_rejected",
            trading_day=True,
            provider=provider_result.provider_name,
            raw_bars=len(raw_bars),
            prepared_bars=len(prepared_bars),
            candidate_signals=len(signal_result.signals),
            approved_signals=len(supervisor_result.approved_signals),
            rejected_signals=len(supervisor_result.rejected_signals),
            notifications=notification_count,
            lake_writes=lake_writes,
            trace_count=len(supervisor_result.traces),
            errors=supervisor_result.errors,
        )
        self._record_worker_health(
            summary,
            details={
                "provider": provider_result.provider_name,
                "fallback_used": provider_result.fallback_used,
                "provider_request_id": provider_result.request_id,
                "provider_compare_status": compare_result.status,
                "provider_compare_issues": len(compare_result.issues),
                "abnormal_bar_count": len(quarantine_result.quarantined),
                "instance_id": self.identity.instance_id,
                "host_id": self.identity.host_id,
                "lock_owner": self.identity.lock_owner(),
                "multi_instance_enabled": self.identity.multi_instance_enabled,
            },
        )
        return summary

    def _record_worker_health(self, summary: WorkerTickSummary, *, details: dict[str, object] | None = None) -> None:
        record_health_metric(
            self.connection,
            module="worker",
            heartbeat_at=self.now_fn(),
            data_latency_sec=0,
            error_rate=0 if not summary.errors else 1,
            consecutive_failures=0 if not summary.errors else 1,
            alert_failures=0,
            core_module_running=True,
            details={
                "pipeline": "market_watch",
                "tick_status": summary.status,
                "trading_day": summary.trading_day,
                **(details or {}),
            },
            thresholds=self.thresholds,
        )


__all__ = ["WorkerPipeline", "WorkerTickSummary"]
