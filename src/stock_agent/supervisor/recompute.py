"""Independent indicator recomputation for supervisor validation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from stock_agent.schemas import Bar, Signal
from stock_agent.strategies.active_j import ACTIVE_J_STRATEGY_ID
from stock_agent.strategies.boll import BOLL_STRATEGY_ID
from stock_agent.strategies.kdj import KDJ_STRATEGY_ID
from stock_agent.strategies.macd import MACD_STRATEGY_ID
from stock_agent.strategies.ma_cross import MA_CROSS_STRATEGY_ID
from stock_agent.strategies.ma_cross_demo import MA_CROSS_DEMO_STRATEGY_ID

RecomputeStatus = Literal["match", "mismatch", "skipped"]


@dataclass(frozen=True)
class RecomputeCheck:
    signal_id: str
    strategy_id: str
    status: RecomputeStatus
    expected_direction: str | None
    actual_direction: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


def validate_recomputed_signals(
    *,
    bars: list[Bar],
    signals: list[Signal],
    strategy_params: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[RecomputeCheck], list[str]]:
    checks = [
        recompute_signal(signal, bars=bars, strategy_params=strategy_params or {})
        for signal in signals
    ]
    errors = [
        f"supervisor recompute mismatch for {check.signal_id}: {check.reason}"
        for check in checks
        if check.status == "mismatch"
    ]
    return checks, errors


def recompute_signal(
    signal: Signal,
    *,
    bars: list[Bar],
    strategy_params: dict[str, dict[str, Any]] | None = None,
) -> RecomputeCheck:
    params = strategy_params or {}
    symbol_bars = sorted([bar for bar in bars if bar.symbol == signal.symbol], key=lambda bar: bar.timestamp)
    index = next((idx for idx, bar in enumerate(symbol_bars) if bar.timestamp == signal.timestamp), None)
    if index is None:
        return _mismatch(signal, None, "signal timestamp not found in supervisor bars")

    try:
        if signal.strategy_id == MA_CROSS_DEMO_STRATEGY_ID:
            expected, details = _ma_cross_direction(symbol_bars, index, short=2, long=3, source_mode="current")
        elif signal.strategy_id == MA_CROSS_STRATEGY_ID:
            short, long = _ma_pair_from_signal(signal)
            expected, details = _ma_cross_direction(symbol_bars, index, short=short, long=long, source_mode="previous")
        elif signal.strategy_id == MACD_STRATEGY_ID:
            active = params.get(MACD_STRATEGY_ID, {})
            expected, details = _macd_direction(
                symbol_bars,
                index,
                fast=int(active.get("fast", 12)),
                slow=int(active.get("slow", 26)),
                signal_period=int(active.get("signal", 9)),
            )
        elif signal.strategy_id == KDJ_STRATEGY_ID:
            active = params.get(KDJ_STRATEGY_ID, {})
            expected, details = _kdj_direction(
                symbol_bars,
                index,
                window=int(active.get("window", 9)),
                k_smoothing=int(active.get("k_smoothing", 3)),
                d_smoothing=int(active.get("d_smoothing", 3)),
            )
        elif signal.strategy_id == BOLL_STRATEGY_ID:
            active = params.get(BOLL_STRATEGY_ID, {})
            expected, details = _boll_direction(
                symbol_bars,
                index,
                window=int(active.get("window", 20)),
                baseline_window=int(active.get("bandwidth_baseline_window", 20)),
            )
        elif signal.strategy_id == ACTIVE_J_STRATEGY_ID:
            active = params.get(ACTIVE_J_STRATEGY_ID, {})
            expected, details = _active_j_direction(
                symbol_bars,
                index,
                j_threshold=float(active.get("j_threshold", 20.0)),
                ma_window=int(active.get("ma_window", 80)),
                kdj_window=int(active.get("kdj_window", 9)),
                k_smoothing=int(active.get("k_smoothing", 3)),
                d_smoothing=int(active.get("d_smoothing", 3)),
            )
        else:
            return RecomputeCheck(
                signal_id=signal.signal_id,
                strategy_id=signal.strategy_id,
                status="skipped",
                expected_direction=None,
                actual_direction=signal.direction,
                reason=f"unsupported strategy for recompute: {signal.strategy_id}",
            )
    except (ValueError, ZeroDivisionError) as exc:
        return _mismatch(signal, None, f"recompute failed: {exc}")

    if expected == signal.direction:
        return RecomputeCheck(
            signal_id=signal.signal_id,
            strategy_id=signal.strategy_id,
            status="match",
            expected_direction=expected,
            actual_direction=signal.direction,
            reason="recomputed trigger matches strategy signal",
            details=details,
        )
    return _mismatch(signal, expected, "recomputed trigger does not match strategy signal", details=details)


def _ma_pair_from_signal(signal: Signal) -> tuple[int, int]:
    match = re.search(r"-ma(\d+)-ma(\d+)-", signal.signal_id)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"MA(\d+)\s*[上下]穿\s*MA(\d+)", signal.reason)
    if match:
        return int(match.group(1)), int(match.group(2))
    raise ValueError("could not infer MA pair from signal")


def _ma_cross_direction(
    bars: list[Bar],
    index: int,
    *,
    short: int,
    long: int,
    source_mode: Literal["current", "previous"],
) -> tuple[str | None, dict[str, float]]:
    if index < long:
        return None, {"required_index": long, "index": float(index)}
    previous_long = bars[index - long : index]
    current_long = bars[index - long + 1 : index + 1] if source_mode == "previous" else bars[index - long + 1 : index + 1]
    previous_short = previous_long[-short:]
    current_short = current_long[-short:]
    previous_short_ma = _mean_close(previous_short)
    previous_long_ma = _mean_close(previous_long)
    current_short_ma = _mean_close(current_short)
    current_long_ma = _mean_close(current_long)
    direction = None
    if previous_short_ma <= previous_long_ma and current_short_ma > current_long_ma:
        direction = "buy_watch"
    elif previous_short_ma >= previous_long_ma and current_short_ma < current_long_ma:
        direction = "sell_watch"
    return direction, {
        "previous_short_ma": previous_short_ma,
        "previous_long_ma": previous_long_ma,
        "current_short_ma": current_short_ma,
        "current_long_ma": current_long_ma,
    }


def _macd_direction(
    bars: list[Bar],
    index: int,
    *,
    fast: int,
    slow: int,
    signal_period: int,
) -> tuple[str | None, dict[str, float]]:
    if index < slow + signal_period:
        return None, {"index": float(index), "warmup": float(slow + signal_period + 1)}
    values = _macd_values(bars[: index + 1], fast=fast, slow=slow, signal_period=signal_period)
    previous = values[-2]
    current = values[-1]
    direction = None
    if previous["dif"] <= previous["dea"] and current["dif"] > current["dea"]:
        direction = "buy_watch"
    elif previous["dif"] >= previous["dea"] and current["dif"] < current["dea"]:
        direction = "sell_watch"
    return direction, {"previous_dif": previous["dif"], "previous_dea": previous["dea"], **current}


def _kdj_direction(
    bars: list[Bar],
    index: int,
    *,
    window: int,
    k_smoothing: int,
    d_smoothing: int,
) -> tuple[str | None, dict[str, float]]:
    values = _kdj_values(bars[: index + 1], window=window, k_smoothing=k_smoothing, d_smoothing=d_smoothing)
    if index < window or values[-1] is None or values[-2] is None:
        return None, {"index": float(index), "warmup": float(window + 1)}
    previous = values[-2]
    current = values[-1]
    direction = None
    if previous["k"] <= previous["d"] and current["k"] > current["d"]:
        direction = "buy_watch"
    elif previous["k"] >= previous["d"] and current["k"] < current["d"]:
        direction = "sell_watch"
    return direction, {"previous_k": previous["k"], "previous_d": previous["d"], **current}


def _boll_direction(
    bars: list[Bar],
    index: int,
    *,
    window: int,
    baseline_window: int,
    k: float = 2.0,
) -> tuple[str | None, dict[str, float]]:
    features = _boll_features(bars[: index + 1], window=window, baseline_window=baseline_window, k=k)
    feature = features[-1] if features else None
    if feature is None:
        return None, {"index": float(index), "warmup": float(window + baseline_window + 1)}
    recent = [item for item in features[-3:] if item is not None]
    if len(recent) < 3:
        return None, {"recent_features": float(len(recent))}
    middle = feature["middle"]
    bandwidth = feature["bandwidth"]
    baseline = feature["baseline_bandwidth"]
    widening = bandwidth >= baseline * 1.8 and all(item["bandwidth"] >= item["baseline_bandwidth"] * 0.6 for item in recent)
    stable_narrowing = all(item["baseline_bandwidth"] * 0.8 <= item["bandwidth"] <= item["baseline_bandwidth"] * 1.2 for item in recent)
    middle_oscillation = _middle_oscillation(recent)
    close = bars[index].close
    direction = None
    if widening and close > middle:
        direction = "buy_watch"
    elif stable_narrowing and close < middle and not middle_oscillation:
        direction = "sell_watch"
    elif stable_narrowing:
        direction = "observe"
    return direction, {"middle": middle, "bandwidth": bandwidth, "baseline_bandwidth": baseline}


def _active_j_direction(
    bars: list[Bar],
    index: int,
    *,
    j_threshold: float,
    ma_window: int,
    kdj_window: int,
    k_smoothing: int,
    d_smoothing: int,
) -> tuple[str | None, dict[str, float]]:
    warmup = max(ma_window, kdj_window) + 1
    if index + 1 < warmup:
        return None, {"index": float(index), "warmup": float(warmup)}
    values = _kdj_values(bars[: index + 1], window=kdj_window, k_smoothing=k_smoothing, d_smoothing=d_smoothing)
    current = values[-1]
    if current is None:
        return None, {"index": float(index)}
    direction = "buy_watch" if current["j"] > j_threshold else None
    return direction, {"j": current["j"], "j_threshold": j_threshold, "ma": _mean_close(bars[index - ma_window + 1 : index + 1])}


def _macd_values(bars: list[Bar], *, fast: int, slow: int, signal_period: int) -> list[dict[str, float]]:
    closes = [bar.close for bar in bars]
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [fast_value - slow_value for fast_value, slow_value in zip(ema_fast, ema_slow, strict=True)]
    dea = _ema(dif, signal_period)
    return [{"dif": d, "dea": e, "macd": 2 * (d - e)} for d, e in zip(dif, dea, strict=True)]


def _ema(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    output: list[float] = []
    for value in values:
        output.append(value if not output else alpha * value + (1 - alpha) * output[-1])
    return output


def _kdj_values(bars: list[Bar], *, window: int, k_smoothing: int, d_smoothing: int) -> list[dict[str, float] | None]:
    values: list[dict[str, float] | None] = []
    previous_k = 50.0
    previous_d = 50.0
    for index, bar in enumerate(bars):
        if index + 1 < window:
            values.append(None)
            continue
        window_bars = bars[index - window + 1 : index + 1]
        highest_high = max(item.high for item in window_bars)
        lowest_low = min(item.low for item in window_bars)
        rsv = 50.0 if highest_high == lowest_low else (bar.close - lowest_low) / (highest_high - lowest_low) * 100
        current_k = (1 - 1 / k_smoothing) * previous_k + (1 / k_smoothing) * rsv
        current_d = (1 - 1 / d_smoothing) * previous_d + (1 / d_smoothing) * current_k
        current_j = 3 * current_k - 2 * current_d
        values.append({"rsv": rsv, "k": current_k, "d": current_d, "j": current_j})
        previous_k = current_k
        previous_d = current_d
    return values


def _boll_features(bars: list[Bar], *, window: int, baseline_window: int, k: float) -> list[dict[str, float] | None]:
    features: list[dict[str, float] | None] = []
    raw_features: list[dict[str, float] | None] = []
    for index, bar in enumerate(bars):
        if index + 1 < window:
            raw_features.append(None)
            features.append(None)
            continue
        window_bars = bars[index - window + 1 : index + 1]
        closes = [item.close for item in window_bars]
        middle = sum(closes) / window
        sigma = math.sqrt(sum((close - middle) ** 2 for close in closes) / window)
        upper = middle + k * sigma
        lower = middle - k * sigma
        bandwidth = (upper - lower) / middle if middle != 0 else 0.0
        raw_feature = {"middle": middle, "upper": upper, "lower": lower, "bandwidth": bandwidth, "close": bar.close}
        raw_features.append(raw_feature)
        recent_raw = [item for item in raw_features if item is not None][-baseline_window - 1 : -1]
        if len(recent_raw) < baseline_window:
            features.append(None)
            continue
        baseline = sum(item["bandwidth"] for item in recent_raw) / baseline_window
        features.append({**raw_feature, "baseline_bandwidth": baseline})
    return features


def _middle_oscillation(recent_features: list[dict[str, float]]) -> bool:
    return sum(1 for feature in recent_features if feature["middle"] and abs(feature["close"] - feature["middle"]) / feature["middle"] <= 0.005) >= 2


def _mean_close(bars: list[Bar]) -> float:
    return sum(bar.close for bar in bars) / len(bars)


def _mismatch(signal: Signal, expected: str | None, reason: str, *, details: dict[str, Any] | None = None) -> RecomputeCheck:
    return RecomputeCheck(
        signal_id=signal.signal_id,
        strategy_id=signal.strategy_id,
        status="mismatch",
        expected_direction=expected,
        actual_direction=signal.direction,
        reason=reason,
        details=details or {},
    )


__all__ = ["RecomputeCheck", "validate_recomputed_signals", "recompute_signal"]
