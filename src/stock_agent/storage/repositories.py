"""Repository functions for SQLite-backed online state."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from stock_agent.schemas import HealthMetric, NewsItem, Signal, StrategySnapshot, TraceChain
from stock_agent.security import redact_sensitive, redact_text

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


def insert_strategy_snapshot(connection: sqlite3.Connection, snapshot: StrategySnapshot) -> None:
    payload = _dump_model(snapshot)
    connection.execute(
        """
        INSERT OR REPLACE INTO strategy_snapshots (
            snapshot_id, date, enabled_strategies, strategy_params, symbols,
            data_policy, watch_window, created_at
        ) VALUES (
            :snapshot_id, :date, :enabled_strategies, :strategy_params, :symbols,
            :data_policy, :watch_window, :created_at
        )
        """,
        payload,
    )
    connection.commit()


def get_strategy_snapshot(connection: sqlite3.Connection, snapshot_id: str) -> StrategySnapshot | None:
    row = connection.execute(
        "SELECT * FROM strategy_snapshots WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    if row is None:
        return None
    return _model_from_row(
        StrategySnapshot,
        row,
        json_fields=("enabled_strategies", "strategy_params", "symbols", "data_policy", "watch_window"),
    )


def list_strategy_snapshots(connection: sqlite3.Connection, limit: int = 50) -> list[StrategySnapshot]:
    rows = connection.execute(
        "SELECT * FROM strategy_snapshots ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        _model_from_row(
            StrategySnapshot,
            row,
            json_fields=("enabled_strategies", "strategy_params", "symbols", "data_policy", "watch_window"),
        )
        for row in rows
    ]


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
            "payload": json.dumps(redact_sensitive(payload), ensure_ascii=False, sort_keys=True),
            "retry_count": retry_count,
            "error_msg": redact_text(error_msg),
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


def get_notification(connection: sqlite3.Connection, notification_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM notifications WHERE notification_id = ?",
        (notification_id,),
    ).fetchone()
    if row is None:
        return None
    payload = dict(row)
    payload["payload"] = json.loads(payload["payload"])
    return payload


def list_notifications_by_status(
    connection: sqlite3.Connection,
    *,
    statuses: list[str],
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not statuses:
        return []
    placeholders = ",".join("?" for _status in statuses)
    rows = connection.execute(
        f"""
        SELECT * FROM notifications
        WHERE status IN ({placeholders})
        ORDER BY created_at ASC
        LIMIT ?
        """,
        [*statuses, limit],
    ).fetchall()
    notifications = []
    for row in rows:
        payload = dict(row)
        payload["payload"] = json.loads(payload["payload"])
        notifications.append(payload)
    return notifications


def update_notification_status(
    connection: sqlite3.Connection,
    *,
    notification_id: str,
    status: str,
    retry_count: int,
    error_msg: str | None,
    updated_at: datetime,
) -> None:
    connection.execute(
        """
        UPDATE notifications
        SET status = ?, retry_count = ?, error_msg = ?, updated_at = ?
        WHERE notification_id = ?
        """,
        (
            status,
            retry_count,
            error_msg,
            updated_at.isoformat().replace("+00:00", "Z"),
            notification_id,
        ),
    )
    connection.commit()


def upsert_checkpoint(
    connection: sqlite3.Connection,
    *,
    checkpoint_id: str,
    module: str,
    checkpoint_key: str,
    checkpoint_value: str,
    updated_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO checkpoints (
            checkpoint_id, module, checkpoint_key, checkpoint_value, updated_at
        ) VALUES (
            :checkpoint_id, :module, :checkpoint_key, :checkpoint_value, :updated_at
        )
        """,
        {
            "checkpoint_id": checkpoint_id,
            "module": module,
            "checkpoint_key": checkpoint_key,
            "checkpoint_value": checkpoint_value,
            "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
        },
    )
    connection.commit()


def get_checkpoint(connection: sqlite3.Connection, checkpoint_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM checkpoints WHERE checkpoint_id = ?",
        (checkpoint_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_checkpoints(
    connection: sqlite3.Connection,
    *,
    module: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if module is None:
        rows = connection.execute(
            "SELECT * FROM checkpoints ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM checkpoints WHERE module = ? ORDER BY updated_at DESC LIMIT ?",
            (module, limit),
        ).fetchall()
    return [dict(row) for row in rows]


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
            "before_config": json.dumps(redact_sensitive(before_config), ensure_ascii=False, sort_keys=True),
            "after_config": json.dumps(redact_sensitive(after_config), ensure_ascii=False, sort_keys=True),
            "diff": redact_text(diff),
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
            "details": json.dumps(redact_sensitive(details), ensure_ascii=False, sort_keys=True),
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


def insert_security_audit(
    connection: sqlite3.Connection,
    *,
    timestamp: datetime,
    source: str,
    actor_ref: str | None,
    action: str,
    decision: str,
    reason: str,
    raw_text: str | None,
    details: dict[str, Any] | None = None,
    audit_id: str | None = None,
) -> str:
    resolved_audit_id = audit_id or f"audit-{uuid4().hex[:12]}"
    connection.execute(
        """
        INSERT OR REPLACE INTO security_audit (
            audit_id, timestamp, source, actor_ref, action, decision, reason, raw_text, details
        ) VALUES (
            :audit_id, :timestamp, :source, :actor_ref, :action, :decision, :reason, :raw_text, :details
        )
        """,
        {
            "audit_id": resolved_audit_id,
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "source": source,
            "actor_ref": redact_text(actor_ref),
            "action": action,
            "decision": decision,
            "reason": redact_text(reason) or "",
            "raw_text": redact_text(raw_text),
            "details": json.dumps(redact_sensitive(details or {}), ensure_ascii=False, sort_keys=True),
        },
    )
    connection.commit()
    return resolved_audit_id


def list_security_audit(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM security_audit ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    audit_rows = []
    for row in rows:
        payload = dict(row)
        payload["details"] = json.loads(payload["details"])
        audit_rows.append(payload)
    return audit_rows


def insert_agent_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    source: str,
    raw_text: str,
    parser_name: str,
    intent: dict[str, Any] | None,
    risk: str | None,
    status: str,
    command_preview: str | None,
    output: str | None,
    trace_id: str | None,
    duration_ms: float,
    created_at: datetime,
    updated_at: datetime,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO agent_runs (
            run_id, source, raw_text, parser_name, intent_json, risk, status,
            command_preview, output, trace_id, duration_ms, created_at, updated_at
        ) VALUES (
            :run_id, :source, :raw_text, :parser_name, :intent_json, :risk, :status,
            :command_preview, :output, :trace_id, :duration_ms, :created_at, :updated_at
        )
        """,
        {
            "run_id": run_id,
            "source": source,
            "raw_text": redact_text(raw_text) or "",
            "parser_name": parser_name,
            "intent_json": json.dumps(redact_sensitive(intent), ensure_ascii=False, sort_keys=True)
            if intent is not None
            else None,
            "risk": risk,
            "status": status,
            "command_preview": redact_text(command_preview),
            "output": redact_text(output),
            "trace_id": trace_id,
            "duration_ms": max(0, duration_ms),
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
        },
    )
    connection.commit()


def get_agent_run(connection: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
    return _agent_run_from_row(row) if row is not None else None


def list_agent_runs(connection: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM agent_runs ORDER BY updated_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_agent_run_from_row(row) for row in rows]


def insert_abnormal_bar(
    connection: sqlite3.Connection,
    *,
    quarantine_id: str,
    bar_id: str,
    symbol: str,
    timestamp: datetime,
    window: str,
    reason: str,
    severity: str,
    status: str,
    bar_payload: dict[str, Any],
    created_at: datetime,
    updated_at: datetime,
    reviewed_by: str | None = None,
    review_note: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT OR REPLACE INTO abnormal_bars (
            quarantine_id, bar_id, symbol, timestamp, window, reason, severity, status,
            bar_payload, created_at, updated_at, reviewed_by, review_note
        ) VALUES (
            :quarantine_id, :bar_id, :symbol, :timestamp, :window, :reason, :severity, :status,
            :bar_payload, :created_at, :updated_at, :reviewed_by, :review_note
        )
        """,
        {
            "quarantine_id": quarantine_id,
            "bar_id": bar_id,
            "symbol": symbol,
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "window": window,
            "reason": redact_text(reason) or "",
            "severity": severity,
            "status": status,
            "bar_payload": json.dumps(redact_sensitive(bar_payload), ensure_ascii=False, sort_keys=True),
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "updated_at": updated_at.isoformat().replace("+00:00", "Z"),
            "reviewed_by": redact_text(reviewed_by),
            "review_note": redact_text(review_note),
        },
    )
    connection.commit()


def list_abnormal_bars(
    connection: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if status is None:
        rows = connection.execute(
            "SELECT * FROM abnormal_bars ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = connection.execute(
            "SELECT * FROM abnormal_bars WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return [_abnormal_bar_from_row(row) for row in rows]


def update_abnormal_bar_status(
    connection: sqlite3.Connection,
    *,
    quarantine_id: str,
    status: str,
    reviewed_by: str,
    review_note: str | None,
    updated_at: datetime,
) -> None:
    connection.execute(
        """
        UPDATE abnormal_bars
        SET status = ?, reviewed_by = ?, review_note = ?, updated_at = ?
        WHERE quarantine_id = ?
        """,
        (
            status,
            redact_text(reviewed_by),
            redact_text(review_note),
            updated_at.isoformat().replace("+00:00", "Z"),
            quarantine_id,
        ),
    )
    connection.commit()


def _signal_statistic_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["details"] = json.loads(payload["details"])
    return payload


def _abnormal_bar_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["bar_payload"] = json.loads(payload["bar_payload"])
    return payload


def _config_change_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["before_config"] = json.loads(payload["before_config"]) if payload["before_config"] else None
    payload["after_config"] = json.loads(payload["after_config"]) if payload["after_config"] else None
    return payload


def _agent_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    payload["intent"] = json.loads(payload.pop("intent_json")) if payload["intent_json"] else None
    return payload


def _dump_model(model: BaseModel) -> dict[str, Any]:
    payload = redact_sensitive(model.model_dump(mode="json"))
    for key, value in list(payload.items()):
        if isinstance(value, (dict, list)):
            payload[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return payload


def _model_from_row(model_type: type[SchemaT], row: sqlite3.Row, json_fields: tuple[str, ...]) -> SchemaT:
    payload = dict(row)
    for field in json_fields:
        payload[field] = json.loads(payload[field])
    return model_type.model_validate(payload)
