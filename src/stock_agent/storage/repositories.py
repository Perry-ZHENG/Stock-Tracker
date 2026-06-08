"""Repository functions for SQLite-backed online state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel

from stock_agent.schemas import HealthMetric, NewsItem, Signal, TraceChain

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


def list_config_changes(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM config_changes ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_config_change(
    connection: sqlite3.Connection,
    *,
    change_id: str,
    status: str,
    source: str,
    before_config: dict[str, Any],
    after_config: dict[str, Any],
    diff: str,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO config_changes (
            change_id, status, source, before_config, after_config, diff, created_at, updated_at
        ) VALUES (
            :change_id, :status, :source, :before_config, :after_config, :diff,
            :created_at, :updated_at
        )
        """,
        {
            "change_id": change_id,
            "status": status,
            "source": source,
            "before_config": json.dumps(before_config, ensure_ascii=False, sort_keys=True),
            "after_config": json.dumps(after_config, ensure_ascii=False, sort_keys=True),
            "diff": diff,
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
        },
    )
    connection.commit()


def get_config_change(connection: sqlite3.Connection, change_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM config_changes WHERE change_id = ?",
        (change_id,),
    ).fetchone()
    if row is None:
        return None
    return _config_change_from_row(row)


def update_config_change_status(
    connection: sqlite3.Connection,
    *,
    change_id: str,
    status: str,
    updated_at: datetime,
    diff: str | None = None,
) -> None:
    if diff is None:
        connection.execute(
            "UPDATE config_changes SET status = ?, updated_at = ? WHERE change_id = ?",
            (status, updated_at.isoformat().replace("+00:00", "Z"), change_id),
        )
    else:
        connection.execute(
            "UPDATE config_changes SET status = ?, diff = ?, updated_at = ? WHERE change_id = ?",
            (status, diff, updated_at.isoformat().replace("+00:00", "Z"), change_id),
        )
    connection.commit()


def list_news_items(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM news_items ORDER BY published_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def insert_news_item(connection: sqlite3.Connection, item: NewsItem) -> None:
    payload = _dump_model(item)
    connection.execute(
        """
        INSERT OR REPLACE INTO news_items (
            news_id, symbol, market, title, summary, url, source,
            published_at, retention_level, created_at
        ) VALUES (
            :news_id, :symbol, :market, :title, :summary, :url, :source,
            :published_at, :retention_level, :created_at
        )
        """,
        payload,
    )
    connection.commit()


def list_recent_news_items(
    connection: sqlite3.Connection,
    *,
    symbols: list[str],
    since: datetime,
    limit: int = 50,
) -> list[NewsItem]:
    symbol_filter = [symbol.upper() for symbol in symbols]
    if symbol_filter:
        placeholders = ",".join("?" for _ in symbol_filter)
        rows = connection.execute(
            f"""
            SELECT * FROM news_items
            WHERE created_at >= ?
              AND (symbol IS NULL OR UPPER(symbol) IN ({placeholders}))
            ORDER BY published_at DESC
            LIMIT ?
            """,
            [since.isoformat().replace("+00:00", "Z"), *symbol_filter, limit],
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT * FROM news_items
            WHERE created_at >= ?
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (since.isoformat().replace("+00:00", "Z"), limit),
        ).fetchall()
    return [_model_from_row(NewsItem, row, json_fields=()) for row in rows]


def insert_signal_statistic(
    connection: sqlite3.Connection,
    *,
    statistic_id: str,
    period: str,
    period_start: datetime,
    period_end: datetime,
    generated_at: datetime,
    signal_count: int,
    trigger_count: int,
    run_count: int,
    hit_count: int | None,
    details: dict[str, Any],
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO signal_statistics (
            statistic_id, period, period_start, period_end, generated_at,
            signal_count, trigger_count, run_count, hit_count, details
        ) VALUES (
            :statistic_id, :period, :period_start, :period_end, :generated_at,
            :signal_count, :trigger_count, :run_count, :hit_count, :details
        )
        """,
        {
            "statistic_id": statistic_id,
            "period": period,
            "period_start": period_start.isoformat().replace("+00:00", "Z"),
            "period_end": period_end.isoformat().replace("+00:00", "Z"),
            "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
            "signal_count": signal_count,
            "trigger_count": trigger_count,
            "run_count": run_count,
            "hit_count": hit_count,
            "details": json.dumps(details, ensure_ascii=False, sort_keys=True),
        },
    )
    connection.commit()


def list_signal_statistics(
    connection: sqlite3.Connection,
    *,
    period: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if period is None:
        rows = connection.execute(
            "SELECT * FROM signal_statistics ORDER BY period_start DESC, generated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT * FROM signal_statistics
            WHERE period = ?
            ORDER BY period_start DESC, generated_at DESC
            LIMIT ?
            """,
            (period, limit),
        ).fetchall()
    return [_signal_statistic_from_row(row) for row in rows]


def _signal_statistic_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["details"] = json.loads(payload["details"])
    return payload


def _config_change_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["before_config"] = json.loads(payload["before_config"]) if payload["before_config"] else None
    payload["after_config"] = json.loads(payload["after_config"]) if payload["after_config"] else None
    return payload


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
