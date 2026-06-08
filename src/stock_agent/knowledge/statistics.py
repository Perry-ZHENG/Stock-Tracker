"""Signal and runtime statistics without PnL semantics."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from stock_agent.storage.repositories import insert_signal_statistic, list_health_metrics, list_signals
from stock_agent.tracing import utc_now

StatisticPeriod = Literal["day", "month", "year"]


@dataclass(frozen=True)
class SignalStatistic:
    statistic_id: str
    period: StatisticPeriod
    period_start: datetime
    period_end: datetime
    generated_at: datetime
    signal_count: int
    trigger_count: int
    run_count: int
    hit_count: int | None
    details: dict[str, object]


def generate_signal_statistics(
    connection: sqlite3.Connection,
    *,
    period: StatisticPeriod,
    anchor: datetime | None = None,
) -> SignalStatistic:
    anchor_time = (anchor or utc_now()).astimezone(UTC)
    period_start, period_end = _period_bounds(period, anchor_time)
    signals = [
        signal
        for signal in list_signals(connection, limit=10_000)
        if period_start <= signal.timestamp < period_end
    ]
    health_metrics = [
        metric
        for metric in list_health_metrics(connection, limit=10_000)
        if period_start <= metric.timestamp < period_end
    ]
    direction_counts = Counter(signal.direction for signal in signals)
    strategy_counts = Counter(signal.strategy_id for signal in signals)
    symbol_counts = Counter(signal.symbol for signal in signals)
    run_status_counts = Counter(metric.status for metric in health_metrics)
    generated_at = utc_now()
    return SignalStatistic(
        statistic_id=f"stat-{period}-{period_start.strftime('%Y%m%dT%H%M%SZ')}",
        period=period,
        period_start=period_start,
        period_end=period_end,
        generated_at=generated_at,
        signal_count=len(signals),
        trigger_count=sum(1 for signal in signals if signal.direction in {"buy_watch", "sell_watch"}),
        run_count=len(health_metrics),
        hit_count=None,
        details={
            "direction_counts": dict(direction_counts),
            "strategy_counts": dict(strategy_counts),
            "symbol_counts": dict(symbol_counts),
            "run_status_counts": dict(run_status_counts),
            "hit_count_status": "reserved_not_calculated",
            "excluded_metrics": ["returns", "holdings", "PnL"],
        },
    )


def persist_signal_statistics(
    connection: sqlite3.Connection,
    statistic: SignalStatistic,
) -> None:
    insert_signal_statistic(
        connection,
        statistic_id=statistic.statistic_id,
        period=statistic.period,
        period_start=statistic.period_start,
        period_end=statistic.period_end,
        generated_at=statistic.generated_at,
        signal_count=statistic.signal_count,
        trigger_count=statistic.trigger_count,
        run_count=statistic.run_count,
        hit_count=statistic.hit_count,
        details=statistic.details,
    )


def _period_bounds(period: StatisticPeriod, anchor: datetime) -> tuple[datetime, datetime]:
    if period == "day":
        start = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if period == "month":
        start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1)
        else:
            end = start.replace(month=start.month + 1)
        return start, end
    if period == "year":
        start = anchor.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, start.replace(year=start.year + 1)
    raise ValueError(f"unsupported statistic period: {period}")


__all__ = ["SignalStatistic", "generate_signal_statistics", "persist_signal_statistics"]
