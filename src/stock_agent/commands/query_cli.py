"""Read-only CLI query mode."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal, TextIO

from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.news.service import NewsQueryService
from stock_agent.storage.repositories import (
    list_config_changes,
    list_health_metrics,
    list_news_items,
    list_signals,
)
from stock_agent.storage.sqlite import open_database

CliQuery = Literal["signals", "health", "config-changes", "news"]


def run_cli_query(
    root: Path,
    *,
    query: CliQuery | None = None,
    limit: int = 10,
    symbol: str | None = None,
    stream: TextIO | None = None,
) -> int:
    output = stream or sys.stdout
    if query is None:
        output.write("Available queries: signals, health, config-changes, news\n")
        output.write("Example: stock-agent cli signals --limit 5\n")
        output.flush()
        return 0

    config = validate_config(DEFAULT_CONFIG)
    sqlite_path = root / config.storage.sqlite_path
    if not sqlite_path.exists():
        output.write(f"query_error=no runtime database\nsqlite_path={sqlite_path}\n")
        output.flush()
        return 1

    connection = open_database(sqlite_path)
    if query == "signals":
        output.write(_format_signals(list_signals(connection, limit=limit)))
    elif query == "health":
        output.write(_format_health(list_health_metrics(connection, limit=limit)))
    elif query == "config-changes":
        output.write(_format_config_changes(list_config_changes(connection, limit=limit)))
    elif query == "news":
        service = NewsQueryService(connection, config=config.news)
        result = service.query(symbols=[symbol] if symbol else [], limit=limit)
        if result.ok:
            output.write(result.message + "\n")
            output.write(_format_news([item.model_dump(mode="json") for item in result.items]))
        else:
            output.write(result.message + "\n")
            output.write(_format_news(list_news_items(connection, limit=limit)))
    else:
        output.write(f"query_error=unsupported query {query}\n")
        output.flush()
        return 1

    output.flush()
    return 0


def _format_signals(rows) -> str:
    if not rows:
        return "signals: no rows\n"
    lines = ["timestamp | symbol | strategy_id | direction | strength | confidence | reason"]
    for signal in rows:
        lines.append(
            " | ".join(
                [
                    signal.timestamp.isoformat().replace("+00:00", "Z"),
                    signal.symbol,
                    signal.strategy_id,
                    signal.direction,
                    f"{signal.strength:.2f}",
                    f"{signal.confidence:.2f}",
                    signal.reason,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _format_health(rows) -> str:
    if not rows:
        return "health: no rows\n"
    lines = ["timestamp | module | status | heartbeat_at | data_latency_sec | error_rate | consecutive_failures | alert_failures"]
    for metric in rows:
        heartbeat = metric.heartbeat_at.isoformat().replace("+00:00", "Z") if metric.heartbeat_at else "none"
        lines.append(
            " | ".join(
                [
                    metric.timestamp.isoformat().replace("+00:00", "Z"),
                    metric.module,
                    metric.status,
                    heartbeat,
                    f"{metric.data_latency_sec:.2f}",
                    f"{metric.error_rate:.4f}",
                    str(metric.consecutive_failures),
                    str(metric.alert_failures),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _format_config_changes(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "config_changes: no rows\n"
    lines = ["created_at | change_id | status | source | diff"]
    for row in rows:
        lines.append(
            " | ".join(
                [
                    str(row["created_at"]),
                    str(row["change_id"]),
                    str(row["status"]),
                    str(row["source"]),
                    str(row.get("diff") or ""),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _format_news(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "news: no rows\n"
    lines = ["published_at | symbol | title | source | url"]
    for row in rows:
        lines.append(
            " | ".join(
                [
                    str(row["published_at"]),
                    str(row.get("symbol") or ""),
                    str(row["title"]),
                    str(row["source"]),
                    str(row["url"]),
                ]
            )
        )
    return "\n".join(lines) + "\n"


__all__ = ["CliQuery", "run_cli_query"]
