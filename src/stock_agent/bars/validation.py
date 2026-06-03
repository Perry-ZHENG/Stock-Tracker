"""Validation and session filtering for standardized bars."""

from __future__ import annotations

from datetime import time
from zoneinfo import ZoneInfo

from stock_agent.schemas import Bar

MARKET_TIMEZONE = ZoneInfo("America/New_York")
REGULAR_SESSION_START = time(9, 30)
REGULAR_SESSION_END = time(16, 0)


class BarValidationError(ValueError):
    """Raised when a bar cannot safely enter strategy calculation."""


def generate_bar_id(symbol: str, interval: str, timestamp: str, source: str) -> str:
    return f"{symbol}-{interval}-{timestamp}-{source}"


def validate_bar(bar: Bar) -> Bar:
    if bar.high < bar.low:
        raise BarValidationError(f"bar {bar.bar_id} has high below low")
    if not (bar.low <= bar.open <= bar.high):
        raise BarValidationError(f"bar {bar.bar_id} has open outside high/low range")
    if not (bar.low <= bar.close <= bar.high):
        raise BarValidationError(f"bar {bar.bar_id} has close outside high/low range")
    if bar.vwap is not None and not (bar.low <= bar.vwap <= bar.high):
        raise BarValidationError(f"bar {bar.bar_id} has vwap outside high/low range")

    expected_bar_id = generate_bar_id(
        symbol=bar.symbol,
        interval=bar.interval,
        timestamp=bar.timestamp.isoformat().replace("+00:00", "Z"),
        source=bar.source,
    )
    if bar.bar_id != expected_bar_id:
        raise BarValidationError(
            f"bar {bar.bar_id} has non-deterministic id; expected {expected_bar_id}"
        )
    return bar


def validate_bars(bars: list[Bar]) -> list[Bar]:
    return [validate_bar(bar) for bar in bars]


def is_regular_session_bar(bar: Bar) -> bool:
    local_timestamp = bar.timestamp.astimezone(MARKET_TIMEZONE)
    local_time = local_timestamp.time()
    return REGULAR_SESSION_START <= local_time <= REGULAR_SESSION_END


def filter_regular_session(bars: list[Bar]) -> list[Bar]:
    return [bar for bar in bars if is_regular_session_bar(bar)]
