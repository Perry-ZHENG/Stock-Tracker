"""Offline demo command pipeline."""

from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from stock_agent.bars import BarBuilder
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.health import HealthThresholds, record_health_metric
from stock_agent.notifications import (
    CliNotificationSink,
    RepositoryNotificationSink,
    persist_approved_signals,
    send_with_retries,
)
from stock_agent.providers.csv_demo import CsvDemoProvider
from stock_agent.schemas import Signal, TraceChain
from stock_agent.storage.repositories import insert_trace_chain
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.strategies.ma_cross_demo import generate_ma_cross_demo_signals
from stock_agent.supervisor import supervise_candidate_signals
from stock_agent.supervisor.checks import signal_traces

EXPECTED_SIGNAL_PATH = Path("tests/fixtures/expected_signals/ma_cross_demo_2_3.json")


@dataclass(frozen=True)
class RunDemoSummary:
    bars_read: int
    bars_used: int
    candidate_signals: int
    approved_signals: int
    rejected_signals: int
    notifications: int
    sqlite_path: Path


def run_demo(
    root: Path,
    *,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> RunDemoSummary:
    output = stream or sys.stdout
    config_context = config_context or load_config(root)
    config = config_context.config
    sqlite_path = root / config.storage.sqlite_path
    connection = initialize_runtime_database(root, config)

    raw_bars = CsvDemoProvider(root / config.provider.csv_demo.path).fetch_intraday_bars(
        interval=config.bar.interval
    )
    bars = BarBuilder(regular_session_only=config.bar.session == "regular_only").from_standard_bars(
        raw_bars
    )
    candidate_signals = generate_ma_cross_demo_signals(bars)
    traces = signal_traces(candidate_signals)
    expected_signals = _load_expected_signals(root)

    supervisor_result = supervise_candidate_signals(
        bars=bars,
        candidate_signals=candidate_signals,
        traces=traces,
        expected_signals=expected_signals,
        connection=connection,
    )

    _persist_traces(connection, supervisor_result.traces)
    persist_approved_signals(connection, supervisor_result.approved_signals)

    notification_results = [
        send_with_retries(RepositoryNotificationSink(connection), supervisor_result.approved_signals),
        send_with_retries(CliNotificationSink(output), supervisor_result.approved_signals),
    ]
    record_health_metric(
        connection,
        module="run_demo",
        data_latency_sec=0,
        error_rate=0 if supervisor_result.ok else 1,
        consecutive_failures=0 if supervisor_result.ok else 1,
        alert_failures=sum(1 for result in notification_results if not result.success),
        details={
            "provider": config.provider.default,
            "bars_used": len(bars),
            "approved_signals": len(supervisor_result.approved_signals),
        },
        thresholds=HealthThresholds.from_config(config.health),
    )

    summary = RunDemoSummary(
        bars_read=len(raw_bars),
        bars_used=len(bars),
        candidate_signals=len(candidate_signals),
        approved_signals=len(supervisor_result.approved_signals),
        rejected_signals=len(supervisor_result.rejected_signals),
        notifications=sum(1 for result in notification_results if result.success),
        sqlite_path=sqlite_path,
    )
    _print_summary(output, summary)
    return summary


def _load_expected_signals(root: Path) -> list[Signal]:
    payload = json.loads((root / EXPECTED_SIGNAL_PATH).read_text(encoding="utf-8"))
    return [Signal.model_validate(item) for item in payload]


def _persist_traces(connection: sqlite3.Connection, traces: list[TraceChain]) -> None:
    for trace in traces:
        insert_trace_chain(connection, trace)


def _print_summary(stream: TextIO, summary: RunDemoSummary) -> None:
    stream.write("Run demo summary\n")
    stream.write(f"bars_read={summary.bars_read}\n")
    stream.write(f"bars_used={summary.bars_used}\n")
    stream.write(f"candidate_signals={summary.candidate_signals}\n")
    stream.write(f"approved_signals={summary.approved_signals}\n")
    stream.write(f"rejected_signals={summary.rejected_signals}\n")
    stream.write(f"notifications={summary.notifications}\n")
    stream.write(f"sqlite_path={summary.sqlite_path}\n")
    stream.flush()


__all__ = ["RunDemoSummary", "run_demo"]
