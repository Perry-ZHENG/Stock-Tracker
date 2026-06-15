"""Abnormal bar quarantine before strategy calculation."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Literal

from stock_agent.schemas import Bar
from stock_agent.storage.repositories import insert_abnormal_bar, update_abnormal_bar_status
from stock_agent.tracing import utc_now

QuarantineStatus = Literal["quarantined", "accepted", "rejected"]


@dataclass(frozen=True)
class QuarantinedBar:
    quarantine_id: str
    bar: Bar
    reason: str
    severity: str = "unhealthy"
    status: QuarantineStatus = "quarantined"

    @property
    def window(self) -> str:
        return f"{self.bar.symbol}:{self.bar.interval}:{self.bar.timestamp.isoformat().replace('+00:00', 'Z')}"


@dataclass(frozen=True)
class QuarantineResult:
    clean_bars: list[Bar]
    quarantined: list[QuarantinedBar] = field(default_factory=list)

    @property
    def has_abnormal(self) -> bool:
        return bool(self.quarantined)


def quarantine_abnormal_bars(
    bars: list[Bar],
    *,
    jump_threshold_ratio: float = 0.2,
    expected_interval_minutes: int | None = None,
) -> QuarantineResult:
    quarantined_ids: set[str] = set()
    quarantined: list[QuarantinedBar] = []

    for bar in bars:
        reasons = _bar_value_reasons(bar)
        for reason in reasons:
            quarantined.append(_quarantined(bar, reason))
            quarantined_ids.add(bar.bar_id)

    grouped: dict[str, list[tuple[int, Bar]]] = {}
    for index, bar in enumerate(bars):
        grouped.setdefault(bar.symbol, []).append((index, bar))

    for symbol_items in grouped.values():
        seen_timestamps: dict[object, Bar] = {}
        previous_input_timestamp = None
        previous_sorted_bar: Bar | None = None
        for input_index, bar in symbol_items:
            if bar.timestamp in seen_timestamps:
                quarantined.append(_quarantined(bar, "duplicate timestamp"))
                quarantined_ids.add(bar.bar_id)
            seen_timestamps[bar.timestamp] = bar
            if previous_input_timestamp is not None and bar.timestamp < previous_input_timestamp:
                quarantined.append(_quarantined(bar, "out-of-order input"))
                quarantined_ids.add(bar.bar_id)
            previous_input_timestamp = bar.timestamp

        for bar in sorted((item for _idx, item in symbol_items), key=lambda item: item.timestamp):
            if previous_sorted_bar is not None:
                if previous_sorted_bar.close and abs(bar.close - previous_sorted_bar.close) / previous_sorted_bar.close > jump_threshold_ratio:
                    quarantined.append(_quarantined(bar, "price jump exceeds threshold"))
                    quarantined_ids.add(bar.bar_id)
                expected_minutes = expected_interval_minutes or _interval_minutes(bar.interval)
                if expected_minutes is not None and bar.timestamp - previous_sorted_bar.timestamp > timedelta(minutes=expected_minutes * 1.5):
                    quarantined.append(_quarantined(bar, "missing window before bar", severity="degraded"))
                    quarantined_ids.add(bar.bar_id)
            previous_sorted_bar = bar

    clean = [bar for bar in bars if bar.bar_id not in quarantined_ids]
    deduped = _dedupe_quarantine(quarantined)
    return QuarantineResult(clean_bars=clean, quarantined=deduped)


def persist_quarantine_result(connection: sqlite3.Connection, result: QuarantineResult) -> None:
    now = utc_now()
    for item in result.quarantined:
        insert_abnormal_bar(
            connection,
            quarantine_id=item.quarantine_id,
            bar_id=item.bar.bar_id,
            symbol=item.bar.symbol,
            timestamp=item.bar.timestamp,
            window=item.window,
            reason=item.reason,
            severity=item.severity,
            status=item.status,
            bar_payload=item.bar.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
        )


def review_quarantined_bar(
    connection: sqlite3.Connection,
    *,
    quarantine_id: str,
    status: Literal["accepted", "rejected"],
    reviewed_by: str,
    review_note: str | None = None,
) -> None:
    update_abnormal_bar_status(
        connection,
        quarantine_id=quarantine_id,
        status=status,
        reviewed_by=reviewed_by,
        review_note=review_note,
        updated_at=utc_now(),
    )


def _bar_value_reasons(bar: Bar) -> list[str]:
    reasons: list[str] = []
    prices = [bar.open, bar.high, bar.low, bar.close]
    if any(price <= 0 for price in prices):
        reasons.append("zero or negative price")
    if bar.high < bar.low or not (bar.low <= bar.open <= bar.high) or not (bar.low <= bar.close <= bar.high):
        reasons.append("OHLC outside valid range")
    if bar.volume < 0:
        reasons.append("negative volume")
    return reasons


def _quarantined(bar: Bar, reason: str, *, severity: str = "unhealthy") -> QuarantinedBar:
    digest = hashlib.sha1(f"{bar.bar_id}|{reason}".encode("utf-8")).hexdigest()[:12]
    return QuarantinedBar(quarantine_id=f"quarantine-{digest}", bar=bar, reason=reason, severity=severity)


def _dedupe_quarantine(items: list[QuarantinedBar]) -> list[QuarantinedBar]:
    seen: set[str] = set()
    output: list[QuarantinedBar] = []
    for item in items:
        if item.quarantine_id in seen:
            continue
        seen.add(item.quarantine_id)
        output.append(item)
    return output


def _interval_minutes(interval: str) -> int | None:
    if interval.endswith("m"):
        try:
            return int(interval[:-1])
        except ValueError:
            return None
    return None


__all__ = [
    "QuarantineResult",
    "QuarantinedBar",
    "persist_quarantine_result",
    "quarantine_abnormal_bars",
    "review_quarantined_bar",
]
