"""Deterministic, clearly-labelled bars for local V2 workflow demonstrations."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from stock_agent.bars.validation import generate_bar_id
from stock_agent.providers.base import MarketDataProvider
from stock_agent.schemas import Bar

_NEW_YORK = ZoneInfo("America/New_York")
_SESSION_OPEN = time(9, 30)
_SESSION_CLOSE = time(16, 0)


class SyntheticDemoProviderError(ValueError):
    """The synthetic provider was called without a bounded intraday request."""


class SyntheticDemoProviderV2(MarketDataProvider):
    """Generate repeatable demo bars without claiming they are market observations."""

    def fetch_intraday_bars(
        self,
        symbols: list[str] | None = None,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        if not symbols or start is None or end is None or start.tzinfo is None or end.tzinfo is None:
            raise SyntheticDemoProviderError("synthetic_demo requires symbols and a timezone-aware time window")
        if end < start:
            raise SyntheticDemoProviderError("synthetic_demo time window ends before it starts")
        minutes = _interval_minutes(interval)
        slots = _session_slots(start, end, minutes)
        return [
            _bar(symbol=symbol, timestamp=timestamp, interval=f"{minutes}m", index=index)
            for symbol in symbols
            for index, timestamp in enumerate(slots)
        ]

    def fetch_provider_health(self) -> dict[str, str]:
        return {"provider": "synthetic_demo", "status": "demo_only"}


def _interval_minutes(value: str | None) -> int:
    if value is None or not value.endswith("m"):
        raise SyntheticDemoProviderError("synthetic_demo supports minute-based intervals only")
    try:
        minutes = int(value[:-1])
    except ValueError as exc:
        raise SyntheticDemoProviderError("synthetic_demo interval must be a positive integer of minutes") from exc
    if minutes <= 0 or 60 % minutes != 0:
        raise SyntheticDemoProviderError("synthetic_demo interval must divide one hour")
    return minutes


def _session_slots(start: datetime, end: datetime, minutes: int) -> list[datetime]:
    first_day = start.astimezone(_NEW_YORK).date()
    last_day = end.astimezone(_NEW_YORK).date()
    slots: list[datetime] = []
    day = first_day
    while day <= last_day:
        if day.weekday() < 5:
            current = datetime.combine(day, _SESSION_OPEN, tzinfo=_NEW_YORK)
            session_end = datetime.combine(day, _SESSION_CLOSE, tzinfo=_NEW_YORK)
            while current < session_end:
                timestamp = current.astimezone(UTC)
                if start <= timestamp <= end:
                    slots.append(timestamp)
                current += timedelta(minutes=minutes)
        day += timedelta(days=1)
    return slots


def _bar(*, symbol: str, timestamp: datetime, interval: str, index: int) -> Bar:
    seed = int.from_bytes(hashlib.sha256(symbol.encode("utf-8")).digest()[:4], "big")
    base_price = 80 + seed % 320
    phase = (seed % 628) / 100
    drift = 0.00045 * index
    cycle = 0.0035 * math.sin(index / 3 + phase)
    opening = base_price * (1 + drift + cycle)
    closing = opening * (1 + 0.0015 * math.sin(index / 2 + phase / 2))
    high = max(opening, closing) * 1.0018
    low = min(opening, closing) * 0.9982
    volume = int((700_000 + seed % 500_000) * (1 + 0.25 * abs(math.sin(index / 4 + phase))))
    timestamp_text = timestamp.isoformat().replace("+00:00", "Z")
    return Bar(
        bar_id=generate_bar_id(symbol, interval, timestamp_text, "synthetic_demo"),
        symbol=symbol,
        timestamp=timestamp,
        interval=interval,
        open=round(opening, 4),
        high=round(high, 4),
        low=round(low, 4),
        close=round(closing, 4),
        volume=volume,
        vwap=round((opening + high + low + closing) / 4, 4),
        source="synthetic_demo",
        quality_flag="synthetic_demo",
    )


__all__ = ["SyntheticDemoProviderError", "SyntheticDemoProviderV2"]
