"""Chronological, non-overlapping dataset splits for signal validation."""

from __future__ import annotations

from dataclasses import dataclass

from stock_agent.contracts.common import TimeWindow
from stock_agent.schemas import Bar


@dataclass(frozen=True)
class TimeSplit:
    name: str
    bars: list[Bar]
    time_window: TimeWindow


class TimeSplitError(ValueError):
    """Raised when a historical sample cannot form valid chronological splits."""


def split_chronologically(
    bars: list[Bar],
    *,
    discovery_fraction: float,
    validation_fraction: float,
    min_bars_per_split: int,
    timezone: str,
) -> list[TimeSplit]:
    """Split each symbol by time, then combine matching split names without shuffling."""

    by_symbol: dict[str, list[Bar]] = {}
    for bar in bars:
        by_symbol.setdefault(bar.symbol, []).append(bar)
    if not by_symbol:
        raise TimeSplitError("no bars are available")
    combined: dict[str, list[Bar]] = {"discovery": [], "validation": [], "holdout": []}
    for symbol, symbol_bars in by_symbol.items():
        ordered = sorted(symbol_bars, key=lambda bar: bar.timestamp)
        minimum = min_bars_per_split * 3
        if len(ordered) < minimum:
            raise TimeSplitError(f"symbol {symbol} has fewer than {minimum} bars required for chronological splits")
        discovery_end = int(len(ordered) * discovery_fraction)
        validation_end = discovery_end + int(len(ordered) * validation_fraction)
        discovery_end = max(min_bars_per_split, discovery_end)
        validation_end = max(discovery_end + min_bars_per_split, validation_end)
        if len(ordered) - validation_end < min_bars_per_split:
            validation_end = len(ordered) - min_bars_per_split
        if discovery_end < min_bars_per_split or validation_end - discovery_end < min_bars_per_split:
            raise TimeSplitError(f"symbol {symbol} cannot satisfy all split minimums")
        combined["discovery"].extend(ordered[:discovery_end])
        combined["validation"].extend(ordered[discovery_end:validation_end])
        combined["holdout"].extend(ordered[validation_end:])
    return [
        TimeSplit(name=name, bars=sorted(values, key=lambda bar: (bar.timestamp, bar.symbol)), time_window=_window(values, timezone))
        for name, values in combined.items()
    ]


def _window(bars: list[Bar], timezone: str) -> TimeWindow:
    if len(bars) < 2:
        raise TimeSplitError("a split requires at least two bars")
    timestamps = sorted(bar.timestamp for bar in bars)
    if timestamps[0] == timestamps[-1]:
        raise TimeSplitError("a split must span more than one timestamp")
    return TimeWindow(from_ts=timestamps[0], to_ts=timestamps[-1], timezone=timezone)


__all__ = ["TimeSplit", "TimeSplitError", "split_chronologically"]
