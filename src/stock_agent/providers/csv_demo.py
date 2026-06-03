"""CSV-backed demo market data provider."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from stock_agent.bars.validation import generate_bar_id
from stock_agent.providers.base import MarketDataProvider
from stock_agent.schemas import Bar

REQUIRED_COLUMNS = (
    "symbol",
    "timestamp",
    "interval",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "source",
)


class CsvDemoProviderError(ValueError):
    """Raised when the CSV demo provider cannot parse demo bars."""


class CsvDemoProvider(MarketDataProvider):
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch_intraday_bars(
        self,
        symbols: list[str] | None = None,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        rows = self._read_rows()
        bars = [self._row_to_bar(row, line_number=index + 2) for index, row in enumerate(rows)]
        return [
            bar
            for bar in bars
            if _matches_filters(bar, symbols=symbols, interval=interval, start=start, end=end)
        ]

    def _read_rows(self) -> list[dict[str, str]]:
        if not self.path.exists():
            raise CsvDemoProviderError(f"CSV demo file not found: {self.path}")

        with self.path.open(newline="", encoding="utf-8") as csv_file:
            reader = csv.DictReader(csv_file)
            if reader.fieldnames is None:
                raise CsvDemoProviderError(f"CSV demo file is empty: {self.path}")
            missing_columns = [column for column in REQUIRED_COLUMNS if column not in reader.fieldnames]
            if missing_columns:
                missing = ", ".join(missing_columns)
                raise CsvDemoProviderError(f"CSV demo file {self.path} is missing columns: {missing}")
            return list(reader)

    def _row_to_bar(self, row: dict[str, str], line_number: int) -> Bar:
        try:
            payload: dict[str, Any] = {
                "bar_id": _bar_id(row),
                "symbol": row["symbol"],
                "timestamp": row["timestamp"],
                "interval": row["interval"],
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "vwap": float(row["vwap"]) if row["vwap"] else None,
                "source": row["source"],
                "quality_flag": "normal",
            }
            return Bar.model_validate(payload)
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise CsvDemoProviderError(
                f"CSV demo file {self.path} has invalid data at line {line_number}: {exc}"
            ) from exc


def _bar_id(row: dict[str, str]) -> str:
    return generate_bar_id(
        symbol=row["symbol"],
        interval=row["interval"],
        timestamp=row["timestamp"],
        source=row["source"],
    )


def _matches_filters(
    bar: Bar,
    symbols: list[str] | None,
    interval: str | None,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if symbols is not None and bar.symbol not in symbols:
        return False
    if interval is not None and bar.interval != interval:
        return False
    if start is not None and bar.timestamp < start:
        return False
    if end is not None and bar.timestamp > end:
        return False
    return True
