"""Read-only historical bar query command."""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.query.service import format_bars, load_bars_from_lake, parse_utc_bound
from stock_agent.schemas import Bar


@dataclass(frozen=True)
class BarQueryResult:
    ok: bool
    bars: list[Bar]
    message: str


def run_bars_query(
    root: Path,
    *,
    symbol: str | None,
    from_value: str | None,
    to_value: str | None,
    stream: TextIO | None = None,
    config_context: RuntimeConfigContext | None = None,
) -> BarQueryResult:
    output = stream or sys.stdout
    config_context = config_context or load_config(root)
    from stock_agent.query import QueryService

    result = QueryService(root, config_context=config_context).execute(
        "bars",
        symbol=symbol,
        from_value=from_value,
        to_value=to_value,
    )
    output.write(result.text)
    output.flush()
    return BarQueryResult(ok=result.ok, bars=[row for row in result.rows if isinstance(row, Bar)], message=result.message or "bars_status=ok")


__all__ = ["BarQueryResult", "format_bars", "load_bars_from_lake", "parse_utc_bound", "run_bars_query"]
