"""Read-only CLI query mode."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Literal, TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.query import QueryService

CliQuery = Literal["signals", "health", "config-changes", "news", "stats", "schedule", "bars"]


def run_cli_query(
    root: Path,
    *,
    query: CliQuery | None = None,
    limit: int = 10,
    symbol: str | None = None,
    period: str = "day",
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
    schedule_date: date | None = None,
    from_value: str | None = None,
    to_value: str | None = None,
) -> int:
    output = stream or sys.stdout
    if query is None:
        output.write("Available queries: signals, health, config-changes, news, stats, schedule, bars, trace\n")
        output.write("Example: stock-agent cli bars --symbol QQQ --from 2026-05-22 --to 2026-05-22\n")
        output.flush()
        return 0

    config_context = config_context or load_config(root)
    result = QueryService(root, config_context=config_context).execute(
        query,
        limit=limit,
        symbol=symbol,
        period=period,
        schedule_date=schedule_date,
        from_value=from_value,
        to_value=to_value,
    )
    output.write(result.text)
    output.flush()
    return 0 if result.ok else 1


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


def _format_statistics(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "statistics: no rows\n"
    lines = ["period | period_start | signal_count | trigger_count | run_count | hit_count_status"]
    for row in rows:
        details = row["details"]
        hit_status = details.get("hit_count_status", "") if isinstance(details, dict) else ""
        lines.append(
            " | ".join(
                [
                    str(row["period"]),
                    str(row["period_start"]),
                    str(row["signal_count"]),
                    str(row["trigger_count"]),
                    str(row["run_count"]),
                    str(hit_status),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _format_schedule(schedule: WatchSchedule) -> str:
    market_day = schedule.market_day
    lines = [
        f"schedule_date={market_day.date}",
        f"timezone={market_day.timezone}",
        f"trading_day={str(market_day.is_trading_day).lower()}",
        f"half_day={str(market_day.is_half_day).lower()}",
    ]
    if market_day.holiday_name:
        lines.append(f"market_note={market_day.holiday_name}")
    if schedule.next_trading_day:
        lines.append(f"next_trading_day={schedule.next_trading_day.date}")
    lines.append("kind | local_start | local_end | utc_start | utc_end | strategy_enabled | note")
    for window in schedule.windows:
        lines.append(
            " | ".join(
                [
                    window.kind,
                    _format_dt(window.start_at),
                    _format_dt(window.end_at),
                    _format_dt(window.start_utc),
                    _format_dt(window.end_utc),
                    str(window.strategy_enabled).lower(),
                    window.note,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _format_dt(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["CliQuery", "run_cli_query"]
