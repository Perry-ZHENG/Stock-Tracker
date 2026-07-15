"""Task-scoped trace links and redacted V2 runtime diagnostics."""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from threading import RLock
from typing import Literal

from pydantic import Field, JsonValue, field_validator

from stock_agent.contracts.common import StrictSchema, ensure_utc
from stock_agent.health.monitor import record_health_metric
from stock_agent.security.redaction import REDACTED, redact_sensitive, redact_text

TraceComponent = Literal["task", "plan", "step", "model", "tool", "mcp", "sandbox", "validation", "report"]
TraceStatus = Literal["success", "failed", "skipped"]
TraceErrorCategory = Literal["model", "tool", "data", "mcp", "sandbox", "validation", "safety", "storage", "unknown"]

_HIDDEN_KEYS = ("prompt", "news_body", "source_code", "candidate_source", "absolute_path", "file_path")
_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:[^\s/]+/)+[^\s/]+")


class AgentTrace(StrictSchema):
    """One safe edge in the task -> plan -> step -> result diagnostic graph."""

    trace_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    component: TraceComponent
    status: TraceStatus
    plan_id: str | None = None
    step_id: str | None = None
    parent_trace_id: str | None = None
    error_category: TraceErrorCategory | None = None
    duration_ms: int = Field(default=0, ge=0)
    input_ref: dict[str, JsonValue] = Field(default_factory=dict)
    output_ref: dict[str, JsonValue] = Field(default_factory=dict)
    error_message: str | None = Field(default=None, max_length=1_000)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _normalize_created_at(cls, value: datetime) -> datetime:
        return ensure_utc(value)  # type: ignore[return-value]


class AgentTraceRecorder:
    """Persist only stable IDs and redacted diagnostics for a V2 task."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._lock = RLock()

    def record(self, trace: AgentTrace) -> AgentTrace:
        safe = trace.model_copy(
            update={
                "input_ref": _safe_ref(trace.input_ref),
                "output_ref": _safe_ref(trace.output_ref),
                "error_message": _safe_error(trace.error_message),
                "error_category": trace.error_category or _classify_error(trace.error_message),
            }
        )
        with self._lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO agent_trace_events (
                    trace_id, task_id, plan_id, step_id, parent_trace_id, component,
                    status, error_category, duration_ms, input_ref_json, output_ref_json,
                    error_message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    safe.trace_id,
                    safe.task_id,
                    safe.plan_id,
                    safe.step_id,
                    safe.parent_trace_id,
                    safe.component,
                    safe.status,
                    safe.error_category,
                    safe.duration_ms,
                    _json(safe.input_ref),
                    _json(safe.output_ref),
                    safe.error_message,
                    _timestamp(safe.created_at),
                ),
            )
            self.connection.commit()
        return safe

    def list_task(self, task_id: str) -> list[AgentTrace]:
        rows = self.connection.execute(
            """
            SELECT * FROM agent_trace_events
            WHERE task_id = ?
            ORDER BY created_at, trace_id
            """,
            (task_id,),
        ).fetchall()
        return [_trace(row) for row in rows]

    def health(self, task_id: str, *, now: datetime | None = None):
        traces = self.list_task(task_id)
        failed = [trace for trace in traces if trace.status == "failed"]
        total = len(traces)
        details = {
            "task_id": task_id,
            "queue_depth": _open_step_count(self.connection, task_id),
            "trace_count": total,
            "failed_trace_count": len(failed),
            "failure_categories": sorted({trace.error_category for trace in failed if trace.error_category}),
            "mcp_available": _component_available(traces, "mcp"),
            "sandbox_healthy": _component_available(traces, "sandbox"),
            "provider_freshness": _provider_freshness(traces),
        }
        return record_health_metric(
            self.connection,
            module="v2_agent_runtime",
            error_rate=len(failed) / total if total else 0,
            consecutive_failures=len(failed),
            core_module_running=True,
            details=details,
            now=now,
        )


def _trace(row) -> AgentTrace:
    import json

    return AgentTrace(
        trace_id=row["trace_id"],
        task_id=row["task_id"],
        plan_id=row["plan_id"],
        step_id=row["step_id"],
        parent_trace_id=row["parent_trace_id"],
        component=row["component"],
        status=row["status"],
        error_category=row["error_category"],
        duration_ms=row["duration_ms"],
        input_ref=json.loads(row["input_ref_json"]),
        output_ref=json.loads(row["output_ref_json"]),
        error_message=row["error_message"],
        created_at=datetime.fromisoformat(str(row["created_at"]).replace("Z", "+00:00")),
    )


def _safe_ref(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    def visit(item, *, key: str | None = None):
        if key is not None and any(marker in key.casefold() for marker in _HIDDEN_KEYS):
            return REDACTED
        if isinstance(item, dict):
            return {str(child_key): visit(child, key=str(child_key)) for child_key, child in item.items()}
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, str):
            redacted = redact_text(item) or ""
            return _ABSOLUTE_PATH.sub("[REDACTED_PATH]", redacted)
        return item

    return redact_sensitive(visit(value))


def _safe_error(value: str | None) -> str | None:
    if value is None:
        return None
    return _ABSOLUTE_PATH.sub("[REDACTED_PATH]", redact_text(value) or "")[:1_000]


def _classify_error(message: str | None) -> TraceErrorCategory | None:
    if not message:
        return None
    normalized = message.casefold()
    for category, markers in {
        "model": ("model", "schema response"),
        "mcp": ("mcp",),
        "sandbox": ("sandbox", "candidate"),
        "validation": ("validation", "claim", "evidence gap"),
        "safety": ("policy", "blocked", "forbidden"),
        "storage": ("sqlite", "database", "artifact", "storage"),
        "data": ("provider", "bar", "news", "evidence"),
        "tool": ("tool",),
    }.items():
        if any(marker in normalized for marker in markers):
            return category  # type: ignore[return-value]
    return "unknown"


def _open_step_count(connection: sqlite3.Connection, task_id: str) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count
        FROM agent_steps
        WHERE task_id = ? AND status IN ('pending', 'running')
        """,
        (task_id,),
    ).fetchone()
    return int(row["count"])


def _component_available(traces: list[AgentTrace], component: str) -> bool | str:
    relevant = [trace for trace in traces if trace.component == component]
    if not relevant:
        return "unknown"
    return not any(trace.status == "failed" for trace in relevant)


def _provider_freshness(traces: list[AgentTrace]) -> str:
    """Providers may publish freshness metadata through safe Tool output references."""

    for trace in reversed(traces):
        value = trace.output_ref.get("provider_freshness")
        if isinstance(value, str):
            return value
    return "unknown"


def _json(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["AgentTrace", "AgentTraceRecorder", "TraceComponent", "TraceErrorCategory", "TraceStatus"]
