"""Pure, reproducible anomaly metrics derived from standardized Bar evidence."""

from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic import Field

from stock_agent.contracts.common import StrictSchema
from stock_agent.schemas import Bar


class AnomalyThresholds(StrictSchema):
    price_return_threshold: float = Field(default=0.03, gt=0, le=1)
    volume_ratio_threshold: float = Field(default=1.8, gt=0, le=100)
    volatility_threshold: float = Field(default=0.02, gt=0, le=1)
    min_baseline_bars: int = Field(default=5, ge=2, le=10_000)


@dataclass(frozen=True)
class AnomalyMetricValues:
    price_return: float
    volume_ratio: float
    realized_volatility: float
    benchmark_relative_return: float | None
    market_anomaly: bool
    triggers: tuple[str, ...]


def calculate_anomaly_metrics(
    current_bars: list[Bar],
    baseline_bars: list[Bar],
    *,
    thresholds: AnomalyThresholds,
    benchmark_bars: list[Bar] | None = None,
) -> AnomalyMetricValues:
    """Calculate directional deviation without inferring a cause or trade action."""

    current = sorted(current_bars, key=lambda bar: bar.timestamp)
    baseline = sorted(baseline_bars, key=lambda bar: bar.timestamp)
    if len(current) < 2:
        raise ValueError("current evidence requires at least two bars")
    if len(baseline) < thresholds.min_baseline_bars:
        raise ValueError("historical baseline has insufficient bars")
    latest = current[-1]
    previous = current[-2]
    price_return = _ratio(latest.close, previous.close) - 1
    volume_ratio = _ratio(latest.volume, sum(bar.volume for bar in baseline) / len(baseline))
    returns = [
        _ratio(current_bar.close, previous_bar.close) - 1
        for previous_bar, current_bar in zip(baseline, baseline[1:])
        if previous_bar.close > 0
    ]
    realized_volatility = math.sqrt(sum(value * value for value in returns) / len(returns)) if returns else 0.0
    benchmark_relative_return = _relative_return(price_return, benchmark_bars)
    triggers: list[str] = []
    if abs(price_return) >= thresholds.price_return_threshold:
        triggers.append("price_return")
    if volume_ratio >= thresholds.volume_ratio_threshold:
        triggers.append("volume_ratio")
    if realized_volatility >= thresholds.volatility_threshold:
        triggers.append("realized_volatility")
    if benchmark_relative_return is not None and abs(benchmark_relative_return) >= thresholds.price_return_threshold:
        triggers.append("benchmark_relative_return")
    return AnomalyMetricValues(
        price_return=price_return,
        volume_ratio=volume_ratio,
        realized_volatility=realized_volatility,
        benchmark_relative_return=benchmark_relative_return,
        market_anomaly=bool(triggers),
        triggers=tuple(triggers),
    )


def _relative_return(price_return: float, benchmark_bars: list[Bar] | None) -> float | None:
    if benchmark_bars is None or len(benchmark_bars) < 2:
        return None
    bars = sorted(benchmark_bars, key=lambda bar: bar.timestamp)
    benchmark_return = _ratio(bars[-1].close, bars[-2].close) - 1
    return price_return - benchmark_return


def _ratio(numerator: float | int, denominator: float | int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


__all__ = ["AnomalyMetricValues", "AnomalyThresholds", "calculate_anomaly_metrics"]
