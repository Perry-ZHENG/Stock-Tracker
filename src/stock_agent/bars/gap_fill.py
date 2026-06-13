"""Gap detection and conservative placeholder bars for replayable windows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from stock_agent.bars.validation import generate_bar_id, validate_bar
from stock_agent.schemas import Bar


@dataclass(frozen=True)
class MissingWindow:
    symbol: str
    interval: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class GapFillPlan:
    symbol: str
    interval: str
    start_at: datetime
    end_at: datetime
    missing_windows: list[MissingWindow]


def detect_missing_windows(
    bars: list[Bar],
    *,
    symbol: str,
    interval: str = "30m",
    start_at: datetime,
    end_at: datetime,
) -> GapFillPlan:
    """Find expected target windows missing from existing bars."""

    existing = {
        bar.timestamp.astimezone(UTC)
        for bar in bars
        if bar.symbol == symbol and bar.interval == interval
    }
    missing = [
        MissingWindow(
            symbol=symbol,
            interval=interval,
            start_at=window_end - timedelta(minutes=_minutes(interval)),
            end_at=window_end,
        )
        for window_end in expected_window_ends(start_at=start_at, end_at=end_at, interval=interval)
        if window_end not in existing
    ]
    return GapFillPlan(
        symbol=symbol,
        interval=interval,
        start_at=start_at.astimezone(UTC),
        end_at=end_at.astimezone(UTC),
        missing_windows=missing,
    )


def expected_window_ends(
    *,
    start_at: datetime,
    end_at: datetime,
    interval: str = "30m",
) -> list[datetime]:
    minutes = _minutes(interval)
    current = _ceil_to_interval(start_at.astimezone(UTC), minutes=minutes)
    end = end_at.astimezone(UTC)
    values: list[datetime] = []
    while current <= end:
        values.append(current)
        current += timedelta(minutes=minutes)
    return values


def build_interpolated_bar(
    *,
    window: MissingWindow,
    previous_bar: Bar,
    next_bar: Bar,
    source: str = "gap_fill",
) -> Bar:
    """Build a conservative placeholder bar for analysis/replay, never normal signals."""

    close = (previous_bar.close + next_bar.open) / 2
    high = max(previous_bar.close, next_bar.open, close)
    low = min(previous_bar.close, next_bar.open, close)
    timestamp_text = window.end_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return validate_bar(
        Bar(
            bar_id=generate_bar_id(window.symbol, window.interval, timestamp_text, source),
            symbol=window.symbol,
            timestamp=window.end_at,
            interval=window.interval,
            open=previous_bar.close,
            high=high,
            low=low,
            close=close,
            volume=0,
            vwap=None,
            source=source,
            quality_flag="interpolated|missing",
        )
    )


def _ceil_to_interval(timestamp: datetime, *, minutes: int) -> datetime:
    midnight = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = int((timestamp - midnight).total_seconds() // 60)
    if timestamp.second or timestamp.microsecond:
        elapsed_minutes += 1
    bucket = ((elapsed_minutes - 1) // minutes + 1) * minutes if elapsed_minutes else minutes
    return midnight + timedelta(minutes=bucket)


def _minutes(interval: str) -> int:
    if not interval.endswith("m"):
        raise ValueError(f"unsupported minute interval: {interval}")
    return int(interval[:-1])


__all__ = [
    "GapFillPlan",
    "MissingWindow",
    "build_interpolated_bar",
    "detect_missing_windows",
    "expected_window_ends",
]
