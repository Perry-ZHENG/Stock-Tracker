"""Unified query service shared by CLI and Telegram entry points."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.knowledge import generate_signal_statistics, persist_signal_statistics
from stock_agent.news.service import NewsQueryService
from stock_agent.scheduler import WatchSchedule, build_watch_schedule
from stock_agent.schemas import Bar, Signal, TraceChain
from stock_agent.storage.repositories import (
    get_signal,
    get_trace_chain,
    list_config_changes,
    list_abnormal_bars,
    list_health_metrics,
    list_news_items,
    list_signal_statistics,
    list_signals,
    list_trace_chain,
)
from stock_agent.storage.sqlite import open_database

QueryName = Literal["signals", "health", "config-changes", "news", "stats", "schedule", "bars", "trace", "provider-compare", "abnormal-bars"]


@dataclass(frozen=True)
class QueryResult:
    ok: bool
    query: QueryName
    text: str
    rows: list[object]
    message: str = ""


class QueryService:
    """Read-oriented query boundary for CLI and Telegram."""

    def __init__(
        self,
        root: Path,
        *,
        config_context: RuntimeConfigContext | None = None,
        allow_cache_writes: bool = True,
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
        period: str = "day",
        schedule_date: date | None = None,
        target_id: str | None = None,
        from_value: str | None = None,
        to_value: str | None = None,
        output_format: Literal["text", "telegram"] = "text",
    ) -> QueryResult:
        if query == "schedule":
            target_date = schedule_date or datetime.now(ZoneInfo(self.config.schedule.timezone)).date()
            text = _format_schedule(build_watch_schedule(config=self.config.schedule, target_date=target_date))
            return QueryResult(ok=True, query=query, text=_format_for_channel(text, output_format), rows=[])

        if query == "bars":
            try:
                start_at = parse_utc_bound(from_value, end=False)
                end_at = parse_utc_bound(to_value, end=True)
            except ValueError as exc:
                text = f"bars_error=invalid time range\nerror={exc}\n"
                return QueryResult(ok=False, query=query, text=text, rows=[], message=str(exc))
            if not symbol:
                text = "bars_error=missing symbol; usage: stock-agent cli bars --symbol SYMBOL --from ... --to ...\n"
                return QueryResult(ok=False, query=query, text=text, rows=[], message="missing symbol")
            bars = load_bars_from_lake(
                self.root / self.config.storage.parquet_root,
                symbol=symbol,
                start_at=start_at,
                end_at=end_at,
            )
            return QueryResult(ok=True, query=query, text=_format_for_channel(format_bars(bars), output_format), rows=bars)

        sqlite_path = self.root / self.config.storage.sqlite_path
        if not sqlite_path.exists():
            prefix = "trace_error" if query == "trace" else "query_error"
            text = f"{prefix}=no runtime database\nsqlite_path={sqlite_path}\n"
            return QueryResult(ok=False, query=query, text=text, rows=[], message="no runtime database")

        connection = open_database(sqlite_path)
        try:
            if query == "signals":
                rows = list_signals(connection, limit=limit)
                text = _format_signals(rows)
            elif query == "health":
                rows = list_health_metrics(connection, limit=limit)
                text = _format_health(rows)
            elif query == "config-changes":
                rows = list_config_changes(connection, limit=limit)
                text = _format_config_changes(rows)
            elif query == "news":
                service = NewsQueryService(connection, config=self.config.news)
                result = service.query(symbols=[symbol] if symbol else [], limit=limit)
                if result.ok:
                    rows = [item.model_dump(mode="json") for item in result.items]
                    text = result.message + "\n" + _format_news(rows)
                else:
                    rows = list_news_items(connection, limit=limit)
                    text = result.message + "\n" + _format_news(rows)
            elif query == "stats":
                if period not in {"day", "month", "year"}:
                    text = f"query_error=unsupported stats period {period}\n"
                    return QueryResult(ok=False, query=query, text=text, rows=[], message="unsupported period")
                statistic = generate_signal_statistics(connection, period=period)  # type: ignore[arg-type]
                if self.allow_cache_writes:
                    persist_signal_statistics(connection, statistic)
                rows = list_signal_statistics(connection, period=period, limit=limit)
                text = _format_statistics(rows)
            elif query == "trace":
                trace_result = _query_trace(connection, target_id)
                return trace_result
            elif query == "provider-compare":
                rows = [trace for trace in list_trace_chain(connection, limit=limit) if trace.module == "provider_compare"]
                text = _format_provider_compare(rows)
            elif query == "abnormal-bars":
                rows = list_abnormal_bars(connection, limit=limit)
                text = _format_abnormal_bars(rows)
            else:
                text = f"query_error=unsupported query {query}\n"
                return QueryResult(ok=False, query=query, text=text, rows=[], message="unsupported query")
        finally:
            connection.close()

        return QueryResult(ok=True, query=query, text=_format_for_channel(text, output_format), rows=list(rows))


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
    bars: list[Bar] = []
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
            bars.append(bar)
    return sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol, bar.bar_id))


def parse_utc_bound(value: str | None, *, end: bool) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    date_only = "T" not in text and " " not in text
    if date_only:
        parsed_date = datetime.fromisoformat(text).date()
        parsed = datetime.combine(parsed_date, time.max if end else time.min, tzinfo=UTC)
    else:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_bars(bars: list[Bar]) -> str:
    if not bars:
        return "bars: no rows\n"
    lines = ["timestamp | symbol | interval | open | high | low | close | volume | source | quality_flag | bar_id"]
    for bar in bars:
        lines.append(
            " | ".join(
                [
                    bar.timestamp.isoformat().replace("+00:00", "Z"),
                    bar.symbol,
                    bar.interval,
                    f"{bar.open:.2f}",
                    f"{bar.high:.2f}",
                    f"{bar.low:.2f}",
                    f"{bar.close:.2f}",
                    str(bar.volume),
                    bar.source,
                    bar.quality_flag,
                    bar.bar_id,
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _query_trace(connection, target_id: str | None) -> QueryResult:
    if not target_id:
        text = "trace_error=missing id; usage: stock-agent cli trace SIGNAL_ID|TRACE_ID\n"
        return QueryResult(ok=False, query="trace", text=text, rows=[], message="missing id")
    signal = get_signal(connection, target_id)
    trace_id = signal.trace_id if signal is not None else target_id
    trace = get_trace_chain(connection, trace_id)
    if signal is None and trace is not None:
        signal = _signal_from_trace_output(connection, trace)
    if signal is None and trace is None:
        text = f"trace_error=not found\nquery_id={target_id}\n"
        return QueryResult(ok=False, query="trace", text=text, rows=[], message="not found")
    rows = [item for item in (signal, trace) if item is not None]
    return QueryResult(ok=True, query="trace", text=_format_trace_detail(query_id=target_id, signal=signal, trace=trace), rows=rows)


def _signal_from_trace_output(connection, trace: TraceChain) -> Signal | None:
    output_ref = trace.output_ref
    signal_id: str | None = None
    if isinstance(output_ref, list) and output_ref:
        signal_id = str(output_ref[0])
    elif isinstance(output_ref, dict):
        value = output_ref.get("signal_id")
        if isinstance(value, str):
            signal_id = value
    return get_signal(connection, signal_id) if signal_id else None


def _format_signals(rows) -> str:
    if not rows:
        return "signals: no rows\n"
    lines = ["timestamp | signal_id | symbol | strategy_id | direction | strength | confidence | reason"]
    for signal in rows:
        lines.append(
            " | ".join(
                [
                    signal.timestamp.isoformat().replace("+00:00", "Z"),
                    signal.signal_id,
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
        lines.append(" | ".join([str(row["created_at"]), str(row["change_id"]), str(row["status"]), str(row["source"]), str(row.get("diff") or "")]))
    return "\n".join(lines) + "\n"


def _format_news(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "news: no rows\n"
    lines = ["published_at | symbol | title | source | url"]
    for row in rows:
        lines.append(" | ".join([str(row["published_at"]), str(row.get("symbol") or ""), str(row["title"]), str(row["source"]), str(row["url"])]))
    return "\n".join(lines) + "\n"


def _format_statistics(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "statistics: no rows\n"
    lines = ["period | period_start | signal_count | trigger_count | run_count | hit_count_status"]
    for row in rows:
        details = row["details"]
        hit_status = details.get("hit_count_status", "") if isinstance(details, dict) else ""
        lines.append(" | ".join([str(row["period"]), str(row["period_start"]), str(row["signal_count"]), str(row["trigger_count"]), str(row["run_count"]), str(hit_status)]))
    return "\n".join(lines) + "\n"


def _format_provider_compare(rows: list[TraceChain]) -> str:
    if not rows:
        return "provider_compare: no rows\n"
    lines = ["provider_compare_status=ok", "created_at | trace_id | status | compare_status | compared | skipped | issues"]
    for trace in rows:
        output = trace.output_ref if isinstance(trace.output_ref, dict) else {}
        issues = output.get("issues", [])
        lines.append(
            " | ".join(
                [
                    trace.created_at.isoformat().replace("+00:00", "Z"),
                    trace.trace_id,
                    trace.status,
                    str(output.get("status", "")),
                    str(output.get("compared", "")),
                    str(output.get("skipped", "")),
                    str(len(issues) if isinstance(issues, list) else 0),
                ]
            )
        )
    return "\n".join(lines) + "\n"


def _format_abnormal_bars(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "abnormal_bars: no rows\n"
    lines = ["abnormal_bars_status=ok", "created_at | quarantine_id | status | severity | symbol | window | reason | bar_id"]
    for row in rows:
        lines.append(
            " | ".join(
                [
                    str(row["created_at"]),
                    str(row["quarantine_id"]),
                    str(row["status"]),
                    str(row["severity"]),
                    str(row["symbol"]),
                    str(row["window"]),
                    str(row["reason"]),
                    str(row["bar_id"]),
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


def _format_trace_detail(*, query_id: str, signal: Signal | None, trace: TraceChain | None) -> str:
    lines = ["trace_status=ok", f"query_id={query_id}"]
    if signal is not None:
        lines.extend(
            [
                f"signal_id={signal.signal_id}",
                f"trace_id={signal.trace_id}",
                f"symbol={signal.symbol}",
                f"timestamp={signal.timestamp.isoformat().replace('+00:00', 'Z')}",
                f"strategy_id={signal.strategy_id}",
                f"direction={signal.direction}",
                f"strength={signal.strength:.2f}",
                f"confidence={signal.confidence:.2f}",
                f"data_quality={signal.data_quality}",
                "supervisor_decision=approved",
                "source_bar_ids=" + ",".join(signal.source_bar_ids),
                f"reason={signal.reason}",
            ]
        )
    else:
        lines.append("signal_status=missing")

    if trace is not None:
        lines.extend(
            [
                f"trace_module={trace.module}",
                f"trace_status={trace.status}",
                f"trace_created_at={trace.created_at.isoformat().replace('+00:00', 'Z')}",
                f"trace_input={_format_ref(trace.input_ref)}",
                f"trace_output={_format_ref(trace.output_ref)}",
            ]
        )
        if trace.error_msg:
            lines.append(f"trace_note={trace.error_msg}")
    else:
        lines.append("trace_status=missing")
    return "\n".join(lines) + "\n"


def _format_ref(value) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if isinstance(value, dict):
        return ";".join(f"{key}={value[key]}" for key in sorted(value))
    return str(value)


def _format_dt(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _format_for_channel(text: str, output_format: Literal["text", "telegram"]) -> str:
    if output_format == "telegram":
        return text.strip()
    return text


__all__ = ["QueryName", "QueryResult", "QueryService", "format_bars", "load_bars_from_lake", "parse_utc_bound"]
