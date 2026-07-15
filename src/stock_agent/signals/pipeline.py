"""Signal pipeline for strategy snapshots and trace persistence."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from stock_agent.config import StockAgentConfig
from stock_agent.contracts.evidence import DataEvidence
from stock_agent.schemas import Bar, Signal, StrategySnapshot, TraceChain
from stock_agent.storage.repositories import insert_strategy_snapshot, insert_trace_chain
from stock_agent.strategies.engine import StrategyEngine
from stock_agent.signals.runner import ActiveSignalRunResult, ActiveSignalRunner, RunnerPolicy
from stock_agent.tracing import trace_for_signal, utc_now


@dataclass(frozen=True)
class SignalPipelineResult:
    signals: list[Signal]
    traces: list[TraceChain]
    snapshot: StrategySnapshot


class SignalPipeline:
    """Run strategy engine and persist audit artifacts when a connection exists."""

    def __init__(
        self,
        *,
        config: StockAgentConfig,
        connection: sqlite3.Connection | None = None,
        engine: StrategyEngine | None = None,
        registry_runner: ActiveSignalRunner | None = None,
        runner_policy: RunnerPolicy | None = None,
    ) -> None:
        self.config = config
        self.connection = connection
        self.engine = engine or StrategyEngine(config.strategies)
        self.registry_runner = registry_runner
        self.runner_policy = runner_policy or RunnerPolicy()

    def run(self, bars: list[Bar]) -> SignalPipelineResult:
        engine_result = self.engine.run(bars)
        signal_traces = [trace_for_signal(signal, module="strategy_engine") for signal in engine_result.signals]
        traces = [*engine_result.traces, *signal_traces]
        snapshot = build_strategy_snapshot(
            config=self.config,
            bars=bars,
            enabled_strategies=engine_result.enabled_strategies,
            strategy_params=engine_result.strategy_params,
        )
        result = SignalPipelineResult(
            signals=engine_result.signals,
            traces=traces,
            snapshot=snapshot,
        )
        self.persist_audit(result)
        return result

    def persist_audit(self, result: SignalPipelineResult) -> None:
        if self.connection is None:
            return
        insert_strategy_snapshot(self.connection, result.snapshot)
        for trace in result.traces:
            insert_trace_chain(self.connection, trace)

    def run_registry(self, task_id: str, data_evidence: DataEvidence) -> ActiveSignalRunResult:
        """Explicit evidence-backed bridge for Agent Runtime and future Worker task contexts.

        The legacy Worker has no AgentTask/DataEvidence at this layer, so it must
        not fabricate one just to execute Registry code. Callers selecting
        ``registry`` mode without a configured bridge receive an empty result.
        """

        if self.runner_policy.mode == "legacy" or self.registry_runner is None:
            return ActiveSignalRunResult(active_version_count=0)
        return self.registry_runner.run(task_id, data_evidence)


def build_strategy_snapshot(
    *,
    config: StockAgentConfig,
    bars: list[Bar],
    enabled_strategies: list[str],
    strategy_params: dict[str, dict[str, Any]],
) -> StrategySnapshot:
    created_at = max((bar.timestamp for bar in bars), default=utc_now()).astimezone(UTC)
    symbols = sorted({bar.symbol for bar in bars} or set(config.symbols.default))
    data_policy = {
        "bar_interval": config.bar.interval,
        "bar_session": config.bar.session,
        "include_pre_market": config.bar.include_pre_market,
        "include_after_hours": config.bar.include_after_hours,
        "data_quality": sorted({bar.quality_flag for bar in bars}),
    }
    watch_window = {
        "timezone": config.schedule.timezone,
        "regular_session_start": config.schedule.regular_session_start,
        "regular_session_end": config.schedule.regular_session_end,
    }
    return StrategySnapshot(
        snapshot_id=_snapshot_id(created_at=created_at, symbols=symbols, enabled_strategies=enabled_strategies),
        date=created_at.date(),
        enabled_strategies=enabled_strategies,
        strategy_params=strategy_params,
        symbols=symbols,
        data_policy=data_policy,
        watch_window=watch_window,
        created_at=created_at,
    )


def _snapshot_id(*, created_at, symbols: list[str], enabled_strategies: list[str]) -> str:
    payload = "|".join(
        [
            created_at.isoformat(),
            ",".join(symbols),
            ",".join(enabled_strategies),
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"snapshot-strategy-{digest}"


__all__ = ["SignalPipeline", "SignalPipelineResult", "build_strategy_snapshot"]
