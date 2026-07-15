"""Trace-chain helpers for auditability and regression debugging."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from stock_agent.schemas import TraceChain


def utc_now() -> datetime:
    return datetime.now(UTC)


def create_trace(
    *,
    trace_id: str,
    module: str,
    input_ref: list[Any] | dict[str, Any],
    output_ref: list[Any] | dict[str, Any],
    status: str = "success",
    parent_id: str | None = None,
    error_msg: str | None = None,
    created_at: datetime | None = None,
) -> TraceChain:
    return TraceChain(
        trace_id=trace_id,
        parent_id=parent_id,
        module=module,
        input_ref=input_ref,
        output_ref=output_ref,
        status=status,  # type: ignore[arg-type]
        error_msg=error_msg,
        created_at=created_at or utc_now(),
    )


def failed_trace(
    *,
    trace_id: str,
    module: str,
    input_ref: list[Any] | dict[str, Any],
    error_msg: str,
    parent_id: str | None = None,
    created_at: datetime | None = None,
) -> TraceChain:
    return create_trace(
        trace_id=trace_id,
        parent_id=parent_id,
        module=module,
        input_ref=input_ref,
        output_ref=[],
        status="failed",
        error_msg=error_msg,
        created_at=created_at,
    )


def skipped_trace(
    *,
    trace_id: str,
    module: str,
    input_ref: list[Any] | dict[str, Any],
    reason: str,
    parent_id: str | None = None,
    created_at: datetime | None = None,
) -> TraceChain:
    return create_trace(
        trace_id=trace_id,
        parent_id=parent_id,
        module=module,
        input_ref=input_ref,
        output_ref=[],
        status="skipped",
        error_msg=reason,
        created_at=created_at,
    )
