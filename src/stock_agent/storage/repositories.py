"""Repository functions for SQLite-backed online state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from stock_agent.schemas import HealthMetric, Signal, TraceChain

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def insert_signal(connection: sqlite3.Connection, signal: Signal) -> None:
    payload = _dump_model(signal)
    connection.execute(
        """
        INSERT OR REPLACE INTO signals (
            signal_id, strategy_id, symbol, timestamp, direction, strength, confidence,
            reason, trace_id, source_bar_ids, data_quality, created_at
        ) VALUES (
            :signal_id, :strategy_id, :symbol, :timestamp, :direction, :strength, :confidence,
            :reason, :trace_id, :source_bar_ids, :data_quality, :created_at
        )
        """,
        payload,
    )
    connection.commit()


def get_signal(connection: sqlite3.Connection, signal_id: str) -> Signal | None:
    row = connection.execute("SELECT * FROM signals WHERE signal_id = ?", (signal_id,)).fetchone()
    if row is None:
        return None
    return _model_from_row(Signal, row, json_fields=("source_bar_ids",))


def list_signals(connection: sqlite3.Connection, limit: int = 50) -> list[Signal]:
    rows = connection.execute(
        "SELECT * FROM signals ORDER BY timestamp DESC, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_model_from_row(Signal, row, json_fields=("source_bar_ids",)) for row in rows]


def insert_trace_chain(connection: sqlite3.Connection, trace: TraceChain) -> None:
    payload = _dump_model(trace)
    connection.execute(
        """
        INSERT OR REPLACE INTO trace_chain (
            trace_id, parent_id, module, input_ref, output_ref, status, error_msg, created_at
        ) VALUES (
            :trace_id, :parent_id, :module, :input_ref, :output_ref, :status, :error_msg, :created_at
        )
        """,
        payload,
    )
    connection.commit()


def get_trace_chain(connection: sqlite3.Connection, trace_id: str) -> TraceChain | None:
    row = connection.execute("SELECT * FROM trace_chain WHERE trace_id = ?", (trace_id,)).fetchone()
    if row is None:
        return None
    return _model_from_row(TraceChain, row, json_fields=("input_ref", "output_ref"))


def list_trace_chain(connection: sqlite3.Connection, limit: int = 50) -> list[TraceChain]:
    rows = connection.execute(
        "SELECT * FROM trace_chain ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_model_from_row(TraceChain, row, json_fields=("input_ref", "output_ref")) for row in rows]


def insert_health_metric(connection: sqlite3.Connection, metric: HealthMetric) -> None:
    payload = _dump_model(metric)
    connection.execute(
        """
        INSERT OR REPLACE INTO health_metrics (
            metric_id, timestamp, module, heartbeat_at, data_latency_sec, error_rate,
            consecutive_failures, alert_failures, status, details
        ) VALUES (
            :metric_id, :timestamp, :module, :heartbeat_at, :data_latency_sec, :error_rate,
            :consecutive_failures, :alert_failures, :status, :details
        )
        """,
        payload,
    )
    connection.commit()


def get_health_metric(connection: sqlite3.Connection, metric_id: str) -> HealthMetric | None:
    row = connection.execute("SELECT * FROM health_metrics WHERE metric_id = ?", (metric_id,)).fetchone()
    if row is None:
        return None
    return _model_from_row(HealthMetric, row, json_fields=("details",))


def list_health_metrics(connection: sqlite3.Connection, limit: int = 50) -> list[HealthMetric]:
    rows = connection.execute(
        "SELECT * FROM health_metrics ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_model_from_row(HealthMetric, row, json_fields=("details",)) for row in rows]


def insert_notification(
    connection: sqlite3.Connection,
    *,
    notification_id: str,
    channel: str,
    status: str,
    payload: dict[str, Any],
    retry_count: int,
    error_msg: str | None,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO notifications (
            notification_id, channel, status, payload, retry_count, error_msg, created_at, updated_at
        ) VALUES (
            :notification_id, :channel, :status, :payload, :retry_count, :error_msg,
            :created_at, :updated_at
        )
        """,
        {
            "notification_id": notification_id,
            "channel": channel,
            "status": status,
            "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "retry_count": retry_count,
            "error_msg": error_msg,
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
        },
    )
    connection.commit()


def list_notifications(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    notifications = []
    for row in rows:
        payload = dict(row)
        payload["payload"] = json.loads(payload["payload"])
        notifications.append(payload)
    return notifications


def _dump_model(model: BaseModel) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    for key, value in list(payload.items()):
        if isinstance(value, (dict, list)):
            payload[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return payload


def _model_from_row(model_type: type[SchemaT], row: sqlite3.Row, json_fields: tuple[str, ...]) -> SchemaT:
    payload = dict(row)
    for field in json_fields:
        payload[field] = json.loads(payload[field])
    return model_type.model_validate(payload)
