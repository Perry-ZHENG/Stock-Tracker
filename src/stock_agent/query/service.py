"""Read-only V2 research queries used by the MCP transport."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Literal

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.news.service import NewsQueryService
from stock_agent.schemas import Bar
from stock_agent.storage.sqlite import open_database

QueryName = Literal["bars", "news"]


@dataclass(frozen=True)
class QueryResult:
    ok: bool
    query: QueryName
    text: str
    rows: list[object]
    message: str = ""


class QueryService:
    """Read persisted evidence without creating tasks or modifying market data."""

    def __init__(
        self,
        root: Path,
        *,
        config_context: RuntimeConfigContext | None = None,
        allow_cache_writes: bool = False,
    ) -> None:
        self.root = root
        self.config_context = config_context or load_config(root)
        self.config = self.config_context.config
        self.allow_cache_writes = allow_cache_writes

    def execute(
        self,
        query: QueryName,
        *,
        limit: int = 10,
        symbol: str | None = None,
        from_value: str | None = None,
        to_value: str | None = None,
        output_format: Literal["text", "telegram"] = "text",
    ) -> QueryResult:
        if query == "bars":
            if not symbol:
                return QueryResult(False, query, "bars_error=missing symbol\n", [], "missing symbol")
            try:
                start_at = parse_utc_bound(from_value, end=False)
                end_at = parse_utc_bound(to_value, end=True)
            except ValueError as exc:
                return QueryResult(False, query, f"bars_error={exc}\n", [], str(exc))
            rows = load_bars_from_lake(
                self.root / self.config.storage.parquet_root,
                symbol=symbol,
                start_at=start_at,
                end_at=end_at,
            )
            return QueryResult(True, query, format_bars(rows), rows)

        sqlite_path = self.root / self.config.storage.sqlite_path
        if not sqlite_path.exists():
            return QueryResult(False, query, "news_error=no runtime database\n", [], "no runtime database")
        connection = open_database(sqlite_path)
        try:
            result = NewsQueryService(connection, config=self.config.news).query(
                symbols=[symbol] if symbol else [],
                limit=limit,
            )
        finally:
            connection.close()
        rows = [item.model_dump(mode="json") for item in result.items]
        return QueryResult(result.ok, query, result.message + "\n" + format_news(rows), rows, result.message)


def load_bars_from_lake(
    lake_root: Path,
    *,
    symbol: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> list[Bar]:
    raw_bars_root = lake_root / "raw_bars"
    if not raw_bars_root.exists():
        return []
    normalized_symbol = symbol.upper() if symbol else None
    rows: list[Bar] = []
    for path in sorted(raw_bars_root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            bar = Bar.model_validate(json.loads(line))
            if normalized_symbol and bar.symbol.upper() != normalized_symbol:
                continue
            if start_at and bar.timestamp < start_at:
                continue
            if end_at and bar.timestamp > end_at:
                continue
            rows.append(bar)
    return sorted(rows, key=lambda bar: (bar.timestamp, bar.symbol, bar.bar_id))


def parse_utc_bound(value: str | None, *, end: bool) -> datetime | None:
    if value is None or not value.strip():
        return None
    text = value.strip()
    if "T" not in text and " " not in text:
        value_date = datetime.fromisoformat(text).date()
        return datetime.combine(value_date, time.max if end else time.min, tzinfo=UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC).astimezone(UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def format_bars(rows: list[Bar]) -> str:
    if not rows:
        return "bars: no rows\n"
    lines = ["timestamp | symbol | interval | close | volume | source"]
    for bar in rows:
        lines.append(
            " | ".join(
                [
                    bar.timestamp.isoformat().replace("+00:00", "Z"),
                    bar.symbol,
                    bar.interval,
                    f"{bar.close:.2f}",
                    str(bar.volume),
                    bar.source,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def format_news(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "news: no rows\n"
    return "\n".join(f"{row.get('published_at')} | {row.get('symbol') or '-'} | {row.get('title')}" for row in rows) + "\n"


__all__ = ["QueryResult", "QueryService", "format_bars", "load_bars_from_lake", "parse_utc_bound"]
