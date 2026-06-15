"""Objective supervisor checks before signals are persisted or notified."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from stock_agent.bars.validation import BarValidationError, validate_bars
from stock_agent.health import HealthThresholds, record_health_metric
from stock_agent.schemas import Bar, Signal, TraceChain
from stock_agent.storage.repositories import insert_trace_chain
from stock_agent.supervisor.recompute import validate_recomputed_signals
from stock_agent.tracing import create_trace, failed_trace, skipped_trace, trace_for_signal

DEFAULT_STRATEGY_WARMUP_BARS = {
    "ma_cross_demo_2_3": 4,
}


@dataclass(frozen=True)
class SupervisorResult:
    approved_signals: list[Signal] = field(default_factory=list)
    rejected_signals: list[Signal] = field(default_factory=list)
    traces: list[TraceChain] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def supervise_candidate_signals(
    *,
    bars: list[Bar],
    candidate_signals: list[Signal],
    traces: list[TraceChain],
    expected_signals: list[Signal] | None = None,
    strategy_warmup_bars: dict[str, int] | None = None,
    strategy_params: dict[str, dict[str, object]] | None = None,
    connection: sqlite3.Connection | None = None,
) -> SupervisorResult:
    """Approve candidate signals only after deterministic objective checks pass."""

    warmup_policy = strategy_warmup_bars or DEFAULT_STRATEGY_WARMUP_BARS
    validation_errors: list[str] = []

    try:
        validate_bars(bars)
    except BarValidationError as exc:
        validation_errors.append(f"bar validation failed: {exc}")

    validation_errors.extend(_validate_warmup(bars, candidate_signals, warmup_policy))
    validation_errors.extend(_validate_signal_fields(candidate_signals))
    validation_errors.extend(_validate_trace_chain(candidate_signals, traces))
    recompute_checks, recompute_errors = validate_recomputed_signals(
        bars=bars,
        signals=candidate_signals,
        strategy_params=strategy_params,
    )
    validation_errors.extend(recompute_errors)

    if expected_signals is not None:
        validation_errors.extend(_validate_expected_signals(candidate_signals, expected_signals))

    if validation_errors:
        failure_trace = failed_trace(
            trace_id=_failure_trace_id(validation_errors, bars, candidate_signals),
            module="supervisor",
            input_ref={
                "bar_ids": [bar.bar_id for bar in bars],
                "signal_ids": [signal.signal_id for signal in candidate_signals],
            },
            error_msg="; ".join(validation_errors),
        )
        recompute_trace = _recompute_trace(
            recompute_checks,
            status="failed" if recompute_errors else "success",
            error_msg="; ".join(recompute_errors) if recompute_errors else None,
        )
        _persist_trace_if_requested(connection, failure_trace)
        _persist_trace_if_requested(connection, recompute_trace)
        _record_recompute_health(connection, recompute_checks, recompute_errors)
        return SupervisorResult(
            approved_signals=[],
            rejected_signals=candidate_signals,
            traces=[*traces, recompute_trace, failure_trace],
            errors=validation_errors,
        )

    if not candidate_signals:
        no_signal_trace = skipped_trace(
            trace_id=_no_signal_trace_id(bars),
            module="supervisor",
            input_ref=[bar.bar_id for bar in bars],
            reason="no candidate signals to approve",
        )
        _persist_trace_if_requested(connection, no_signal_trace)
        return SupervisorResult(
            approved_signals=[],
            rejected_signals=[],
            traces=[*traces, no_signal_trace],
            errors=[],
        )

    recompute_trace = _recompute_trace(recompute_checks, status="success", error_msg=None)
    _persist_trace_if_requested(connection, recompute_trace)
    _record_recompute_health(connection, recompute_checks, [])
    return SupervisorResult(
        approved_signals=candidate_signals,
        rejected_signals=[],
        traces=[*traces, recompute_trace],
        errors=[],
    )


def _validate_warmup(
    bars: list[Bar],
    signals: list[Signal],
    warmup_policy: dict[str, int],
) -> list[str]:
    errors: list[str] = []
    for strategy_id in sorted({signal.strategy_id for signal in signals}):
        required_bars = warmup_policy.get(strategy_id)
        if required_bars is not None and len(bars) < required_bars:
            errors.append(
                f"strategy {strategy_id} requires at least {required_bars} bars; got {len(bars)}"
            )
    return errors


def _validate_signal_fields(signals: list[Signal]) -> list[str]:
    errors: list[str] = []
    for signal in signals:
        try:
            Signal.model_validate(signal.model_dump(mode="json"))
        except ValueError as exc:
            errors.append(f"signal {signal.signal_id} failed schema validation: {exc}")
        if not signal.source_bar_ids:
            errors.append(f"signal {signal.signal_id} has empty source_bar_ids")
    return errors


def _validate_trace_chain(signals: list[Signal], traces: list[TraceChain]) -> list[str]:
    errors: list[str] = []
    traces_by_id = {trace.trace_id: trace for trace in traces}

    for signal in signals:
        trace = traces_by_id.get(signal.trace_id)
        if trace is None:
            errors.append(f"signal {signal.signal_id} missing trace {signal.trace_id}")
            continue
        if trace.status != "success":
            errors.append(f"signal {signal.signal_id} trace {trace.trace_id} is {trace.status}")
        if not _trace_output_contains_signal(trace, signal):
            errors.append(f"trace {trace.trace_id} does not output signal {signal.signal_id}")
        if not _trace_input_contains_source_bars(trace, signal):
            errors.append(f"trace {trace.trace_id} does not include all source_bar_ids")

    return errors


def _validate_expected_signals(
    candidate_signals: list[Signal],
    expected_signals: list[Signal],
) -> list[str]:
    if _normalized_signals(candidate_signals) != _normalized_signals(expected_signals):
        return ["expected signal regression mismatch"]
    return []


def _trace_output_contains_signal(trace: TraceChain, signal: Signal) -> bool:
    output_ref = trace.output_ref
    if isinstance(output_ref, list):
        return signal.signal_id in output_ref
    return output_ref.get("signal_id") == signal.signal_id


def _trace_input_contains_source_bars(trace: TraceChain, signal: Signal) -> bool:
    input_ref = trace.input_ref
    if isinstance(input_ref, list):
        return set(signal.source_bar_ids).issubset(set(input_ref))
    bar_ids = input_ref.get("bar_ids")
    if isinstance(bar_ids, list):
        return set(signal.source_bar_ids).issubset(set(bar_ids))
    return False


def _normalized_signals(signals: Iterable[Signal]) -> list[dict[str, object]]:
    return sorted(
        (signal.model_dump(mode="json") for signal in signals),
        key=lambda payload: str(payload["signal_id"]),
    )


def _failure_trace_id(
    errors: list[str],
    bars: list[Bar],
    signals: list[Signal],
) -> str:
    payload = "|".join(
        [
            *errors,
            *[bar.bar_id for bar in bars],
            *[signal.signal_id for signal in signals],
        ]
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"trace-supervisor-failed-{digest}"


def _no_signal_trace_id(bars: list[Bar]) -> str:
    payload = "|".join(bar.bar_id for bar in bars) or "empty"
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"trace-supervisor-skipped-{digest}"


def _persist_trace_if_requested(
    connection: sqlite3.Connection | None,
    trace: TraceChain,
) -> None:
    if connection is not None:
        insert_trace_chain(connection, trace)


def _recompute_trace(recompute_checks, *, status: str, error_msg: str | None) -> TraceChain:
    return create_trace(
        trace_id=_recompute_trace_id(recompute_checks, status),
        module="supervisor_recompute",
        input_ref={"signal_ids": [check.signal_id for check in recompute_checks]},
        output_ref={
            "checks": [
                {
                    "signal_id": check.signal_id,
                    "strategy_id": check.strategy_id,
                    "status": check.status,
                    "expected_direction": check.expected_direction,
                    "actual_direction": check.actual_direction,
                    "reason": check.reason,
                    "details": check.details,
                }
                for check in recompute_checks
            ]
        },
        status=status,
        error_msg=error_msg,
    )


def _recompute_trace_id(recompute_checks, status: str) -> str:
    payload = "|".join([status, *[f"{check.signal_id}:{check.status}:{check.expected_direction}" for check in recompute_checks]]) or status
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"trace-supervisor-recompute-{digest}"


def _record_recompute_health(connection: sqlite3.Connection | None, recompute_checks, recompute_errors: list[str]) -> None:
    if connection is None:
        return
    record_health_metric(
        connection,
        module="supervisor",
        data_latency_sec=0,
        error_rate=1 if recompute_errors else 0,
        consecutive_failures=1 if recompute_errors else 0,
        alert_failures=len(recompute_errors),
        details={
            "check": "independent_recompute",
            "signals_checked": len(recompute_checks),
            "mismatches": len(recompute_errors),
            "rejected_signal_ids": [check.signal_id for check in recompute_checks if check.status == "mismatch"],
        },
        thresholds=HealthThresholds(),
    )


def signal_traces(signals: list[Signal]) -> list[TraceChain]:
    return [trace_for_signal(signal) for signal in signals]


__all__ = [
    "DEFAULT_STRATEGY_WARMUP_BARS",
    "SupervisorResult",
    "signal_traces",
    "supervise_candidate_signals",
]
