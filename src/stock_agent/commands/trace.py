"""Read-only signal trace query command."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.query import QueryService
from stock_agent.schemas import Signal, TraceChain


@dataclass(frozen=True)
class TraceQueryResult:
    ok: bool
    query_id: str
    signal: Signal | None
    trace: TraceChain | None
    message: str


def run_trace_query(
    root: Path,
    query_id: str | None,
    *,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> TraceQueryResult:
    output = stream or sys.stdout
    if not query_id:
        message = "trace_error=missing id; usage: stock-agent cli trace SIGNAL_ID|TRACE_ID"
        output.write(message + "\n")
        output.flush()
        return TraceQueryResult(ok=False, query_id="", signal=None, trace=None, message=message)

    config_context = config_context or load_config(root)
    result = QueryService(root, config_context=config_context).execute("trace", target_id=query_id)
    signal = next((row for row in result.rows if isinstance(row, Signal)), None)
    trace = next((row for row in result.rows if isinstance(row, TraceChain)), None)
    output.write(result.text)
    output.flush()
    return TraceQueryResult(ok=result.ok, query_id=query_id, signal=signal, trace=trace, message=result.message or "trace_status=ok")


__all__ = ["TraceQueryResult", "run_trace_query"]
