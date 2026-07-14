"""Deterministic market features used as evidence, never as trading decisions."""

from __future__ import annotations

import math
from collections import defaultdict

from stock_agent.contracts.evidence import DataFeature
from stock_agent.contracts.common import TimeWindow
from stock_agent.schemas import Bar

SUPPORTED_FEATURES = frozenset(
    {
        "return_change",
        "volume_ratio",
        "realized_volatility",
        "gap",
        "relative_to_baseline",
    }
)


def compute_market_features(
    bars: list[Bar],
    *,
    requested_features: list[str],
    baseline_window: int,
    source_window: TimeWindow,
) -> tuple[list[DataFeature], list[str]]:
    """Return stable feature rows and quality flags for each requested symbol.

    A missing baseline degrades quality rather than inventing a value. The
    caller can still retain the complete Bar artifact for later re-computation.
    """

    requested = requested_features or sorted(SUPPORTED_FEATURES)
    unknown = sorted(set(requested) - SUPPORTED_FEATURES)
    if unknown:
        raise ValueError(f"unsupported market features: {', '.join(unknown)}")

    by_symbol: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        by_symbol[bar.symbol].append(bar)

    features: list[DataFeature] = []
    quality_flags: list[str] = []
    for symbol in sorted(by_symbol):
        rows = sorted(by_symbol[symbol], key=lambda item: item.timestamp)
        if len(rows) < 2:
            quality_flags.append(f"insufficient_bars:{symbol}")
            continue
        baseline = rows[-baseline_window - 1 : -1]
        has_baseline = len(baseline) >= baseline_window
        if not has_baseline and any(
            name in requested for name in {"volume_ratio", "realized_volatility", "relative_to_baseline"}
        ):
            quality_flags.append(f"baseline_insufficient:{symbol}")

        previous = rows[-2]
        latest = rows[-1]
        values: dict[str, float] = {
            "return_change": _ratio(latest.close, previous.close) - 1,
            "gap": _ratio(latest.open, previous.close) - 1,
        }
        if has_baseline:
            values["volume_ratio"] = _ratio(latest.volume, sum(item.volume for item in baseline) / len(baseline))
            values["relative_to_baseline"] = _ratio(
                latest.close,
                sum(item.close for item in baseline) / len(baseline),
            ) - 1
            returns = [
                _ratio(current.close, earlier.close) - 1
                for earlier, current in zip(baseline, baseline[1:])
                if earlier.close > 0
            ]
            if returns:
                values["realized_volatility"] = math.sqrt(sum(value * value for value in returns) / len(returns))

        for name in requested:
            if name not in values:
                continue
            features.append(
                DataFeature(
                    name=f"{symbol}.{name}",
                    value=values[name],
                    unit="ratio",
                    source_window=source_window,
                )
            )
    return features, quality_flags


def _ratio(numerator: float | int, denominator: float | int) -> float:
    if denominator == 0:
        return 0.0
    return float(numerator) / float(denominator)


__all__ = ["SUPPORTED_FEATURES", "compute_market_features"]
