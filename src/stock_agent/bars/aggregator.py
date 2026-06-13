"""Aggregate tick/1m bars into deterministic 30m bars."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Iterable

from stock_agent.bars.validation import generate_bar_id, validate_bar
from stock_agent.schemas import Bar
from stock_agent.storage.repositories import upsert_checkpoint
from stock_agent.tracing import utc_now


@dataclass(frozen=True)
class AggregationWindow:
    symbol: str
    interval: str
    start_at: datetime
    end_at: datetime
    source: str


@dataclass(frozen=True)
class AggregationResult:
    bars: list[Bar]
    windows: list[AggregationWindow]
    skipped_windows: list[AggregationWindow]


def aggregate_to_interval(
    source_bars: list[Bar],
    *,
    target_interval: str = "30m",
    source_interval: str = "1m",
    output_source: str | None = None,
    expected_points: int | None = None,
) -> AggregationResult:
    """Aggregate source bars into fixed-width target bars.

    Window semantics are ``(window_start, window_end]`` and output bar timestamp
    equals ``window_end``.
    """

    if target_interval != "30m":
        raise ValueError(f"unsupported target interval: {target_interval}")
    expected = expected_points or _minutes(target_interval)
    grouped: dict[tuple[str, datetime, str], list[tuple[int, Bar]]] = {}
    for index, bar in enumerate(source_bars):
        if bar.interval != source_interval:
            continue
        window_end = _window_end(bar.timestamp, minutes=_minutes(target_interval))
        source = output_source or f"{bar.source}_agg"
        grouped.setdefault((bar.symbol, window_end, source), []).append((index, bar))

    aggregated: list[Bar] = []
    windows: list[AggregationWindow] = []
    skipped: list[AggregationWindow] = []
    for (symbol, window_end, source), indexed_bars in sorted(grouped.items(), key=lambda item: item[0]):
        window = AggregationWindow(
            symbol=symbol,
            interval=target_interval,
            start_at=window_end - timedelta(minutes=_minutes(target_interval)),
            end_at=window_end,
            source=source,
        )
        windows.append(window)
        if not indexed_bars:
            skipped.append(window)
            continue
        aggregated.append(_aggregate_window(indexed_bars, window=window, expected_points=expected))

    return AggregationResult(
        bars=aggregated,
        windows=windows,
        skipped_windows=skipped,
    )


def update_bar_checkpoint(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    interval: str,
    window_end: datetime,
    module: str = "bar_aggregator",
) -> None:
    timestamp = window_end.astimezone(UTC).isoformat().replace("+00:00", "Z")
    upsert_checkpoint(
        connection,
        checkpoint_id=checkpoint_id(symbol=symbol, interval=interval),
        module=module,
        checkpoint_key=f"{symbol}:{interval}:last_window_end",
        checkpoint_value=timestamp,
        updated_at=utc_now(),
    )


def checkpoint_id(*, symbol: str, interval: str) -> str:
    return f"checkpoint-bar-{symbol}-{interval}"


def _aggregate_window(
    indexed_bars: list[tuple[int, Bar]],
    *,
    window: AggregationWindow,
    expected_points: int,
) -> Bar:
    original_indices = [index for index, _bar in indexed_bars]
    bars = [bar for _index, bar in indexed_bars]
    sorted_bars = sorted(bars, key=lambda bar: bar.timestamp)
    unique_by_timestamp: dict[datetime, Bar] = {}
    for bar in sorted_bars:
        unique_by_timestamp.setdefault(bar.timestamp, bar)
    unique_bars = list(unique_by_timestamp.values())

    open_bar = unique_bars[0]
    close_bar = unique_bars[-1]
    high = max(bar.high for bar in unique_bars)
    low = min(bar.low for bar in unique_bars)
    volume = sum(bar.volume for bar in unique_bars)
    vwap = _weighted_vwap(unique_bars)
    timestamp_text = window.end_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    bar = Bar(
        bar_id=generate_bar_id(window.symbol, window.interval, timestamp_text, window.source),
        symbol=window.symbol,
        timestamp=window.end_at,
        interval=window.interval,
        open=open_bar.open,
        high=high,
        low=low,
        close=close_bar.close,
        volume=volume,
        vwap=vwap,
        source=window.source,
        quality_flag=_quality_flag(
            bars=bars,
            original_indices=original_indices,
            unique_count=len(unique_bars),
            expected_points=expected_points,
        ),
    )
    return validate_bar(bar)


def _weighted_vwap(bars: Iterable[Bar]) -> float | None:
    total_volume = 0
    weighted_sum = 0.0
    for bar in bars:
        if bar.vwap is None:
            return None
        total_volume += bar.volume
        weighted_sum += bar.vwap * bar.volume
    if total_volume == 0:
        return None
    return weighted_sum / total_volume


def _quality_flag(
    *,
    bars: list[Bar],
    original_indices: list[int],
    unique_count: int,
    expected_points: int,
) -> str:
    flags: list[str] = []
    timestamps = [bar.timestamp for bar in bars]
    if len(set(timestamps)) != len(timestamps):
        flags.append("duplicate")
    if original_indices != sorted(original_indices) or timestamps != sorted(timestamps):
        flags.append("out_of_order")
    if unique_count < expected_points:
        flags.append("missing")
    source_flags = sorted({bar.quality_flag for bar in bars if bar.quality_flag != "normal"})
    flags.extend(source_flags)
    return "normal" if not flags else "|".join(dict.fromkeys(flags))


def _window_end(timestamp: datetime, *, minutes: int) -> datetime:
    utc_timestamp = timestamp.astimezone(UTC)
    midnight = utc_timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = int((utc_timestamp - midnight).total_seconds() // 60)
    bucket = ((elapsed_minutes - 1) // minutes + 1) * minutes if elapsed_minutes else minutes
    return midnight + timedelta(minutes=bucket)


def _minutes(interval: str) -> int:
    if not interval.endswith("m"):
        raise ValueError(f"unsupported minute interval: {interval}")
    return int(interval[:-1])


__all__ = [
    "AggregationResult",
    "AggregationWindow",
    "aggregate_to_interval",
    "checkpoint_id",
    "update_bar_checkpoint",
]
