"""Historical bar replay command."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from stock_agent.bars import BarBuilder
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.query.service import load_bars_from_lake, parse_utc_bound
from stock_agent.schemas import Bar, Signal
from stock_agent.signals import SignalPipeline
from stock_agent.storage.repositories import insert_signal
from stock_agent.storage.sqlite import initialize_runtime_database


@dataclass(frozen=True)
class ReplayResult:
    ok: bool
    raw_bars: list[Bar]
    prepared_bars: list[Bar]
    signals: list[Signal]
    persisted: bool
    report_path: Path | None = None


def run_replay(
    root: Path,
    *,
    from_value: str | None,
    to_value: str | None,
    symbols: list[str],
    persist: bool = False,
    report_path: Path | None = None,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> ReplayResult:
    output = stream or sys.stdout
    if not symbols:
        output.write("replay_error=missing symbols; usage: stock-agent replay --symbols QQQ --from ... --to ...\n")
        output.flush()
        return ReplayResult(ok=False, raw_bars=[], prepared_bars=[], signals=[], persisted=False)

    try:
        start_at = parse_utc_bound(from_value, end=False)
        end_at = parse_utc_bound(to_value, end=True)
    except ValueError as exc:
        output.write(f"replay_error=invalid time range\nerror={exc}\n")
        output.flush()
        return ReplayResult(ok=False, raw_bars=[], prepared_bars=[], signals=[], persisted=False)

    config_context = config_context or load_config(root)
    config = config_context.config
    lake_root = root / config.storage.parquet_root
    raw_bars = _load_symbol_bars(lake_root, symbols=symbols, start_at=start_at, end_at=end_at)
    prepared_bars = BarBuilder(regular_session_only=config.bar.session == "regular_only").from_standard_bars(raw_bars)

    connection = initialize_runtime_database(root, config) if persist else None
    try:
        result = SignalPipeline(config=config, connection=connection).run(prepared_bars)
        if connection is not None:
            for signal in result.signals:
                insert_signal(connection, signal)
    finally:
        if connection is not None:
            connection.close()

    resolved_report_path = _write_report(
        report_path,
        root=root,
        raw_bars=raw_bars,
        prepared_bars=prepared_bars,
        signals=result.signals,
        persisted=persist,
    )
    replay_result = ReplayResult(
        ok=True,
        raw_bars=raw_bars,
        prepared_bars=prepared_bars,
        signals=result.signals,
        persisted=persist,
        report_path=resolved_report_path,
    )
    output.write(format_replay_result(replay_result))
    output.flush()
    return replay_result


def format_replay_result(result: ReplayResult) -> str:
    lines = [
        "replay_status=ok",
        f"dry_run={str(not result.persisted).lower()}",
        f"persisted={str(result.persisted).lower()}",
        f"raw_bars={len(result.raw_bars)}",
        f"prepared_bars={len(result.prepared_bars)}",
        f"signals={len(result.signals)}",
    ]
    if result.report_path is not None:
        lines.append(f"report_path={result.report_path}")
    lines.append("signal_id | timestamp | symbol | strategy_id | direction | strength | confidence | reason")
    for signal in result.signals:
        lines.append(
            " | ".join(
                [
                    signal.signal_id,
                    signal.timestamp.isoformat().replace("+00:00", "Z"),
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


def _load_symbol_bars(
    lake_root: Path,
    *,
    symbols: list[str],
    start_at,
    end_at,
) -> list[Bar]:
    bars: list[Bar] = []
    seen: set[str] = set()
    for symbol in symbols:
        for bar in load_bars_from_lake(lake_root, symbol=symbol, start_at=start_at, end_at=end_at):
            if bar.bar_id in seen:
                continue
            seen.add(bar.bar_id)
            bars.append(bar)
    return sorted(bars, key=lambda bar: (bar.timestamp, bar.symbol, bar.bar_id))


def _write_report(
    report_path: Path | None,
    *,
    root: Path,
    raw_bars: list[Bar],
    prepared_bars: list[Bar],
    signals: list[Signal],
    persisted: bool,
) -> Path | None:
    if report_path is None:
        return None
    resolved = report_path if report_path.is_absolute() else root / report_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "report_type": "replay",
        "dry_run": not persisted,
        "persisted": persisted,
        "raw_bar_ids": [bar.bar_id for bar in raw_bars],
        "prepared_bar_ids": [bar.bar_id for bar in prepared_bars],
        "signals": [signal.model_dump(mode="json") for signal in signals],
    }
    resolved.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return resolved


__all__ = ["ReplayResult", "format_replay_result", "run_replay"]
