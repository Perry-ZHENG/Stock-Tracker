"""Bollinger bandwidth strategy for v1 watch signals."""

from __future__ import annotations

import math
from collections import defaultdict

from stock_agent.schemas import Bar, Signal

BOLL_STRATEGY_ID = "boll"


def generate_boll_signals(
    bars: list[Bar],
    *,
    window: int = 20,
    bandwidth_baseline_window: int = 20,
    k: float = 2.0,
) -> list[Signal]:
    _validate_params(window, bandwidth_baseline_window, k)
    signals: list[Signal] = []

    for symbol_bars in _bars_by_symbol(bars).values():
        sorted_bars = sorted(symbol_bars, key=lambda bar: bar.timestamp)
        features = _boll_features(sorted_bars, window=window, baseline_window=bandwidth_baseline_window, k=k)
        for index, feature in enumerate(features):
            if feature is None:
                continue
            recent_features = _recent_features(features, index, count=3)
            if len(recent_features) < 3:
                continue
            signal = _signal_from_feature(
                bars=sorted_bars,
                index=index,
                feature=feature,
                recent_features=recent_features,
                window=window,
                baseline_window=bandwidth_baseline_window,
            )
            if signal is not None:
                signals.append(signal)

    return sorted(signals, key=lambda signal: (signal.timestamp, signal.symbol, signal.signal_id))


def _signal_from_feature(
    *,
    bars: list[Bar],
    index: int,
    feature: dict[str, float],
    recent_features: list[dict[str, float]],
    window: int,
    baseline_window: int,
) -> Signal | None:
    current_bar = bars[index]
    source_bars = bars[index - window - baseline_window + 1 : index + 1]
    middle = feature["middle"]
    bandwidth = feature["bandwidth"]
    baseline = feature["baseline_bandwidth"]
    widening = bandwidth >= baseline * 1.8 and all(
        item["bandwidth"] >= item["baseline_bandwidth"] * 0.6 for item in recent_features
    )
    stable_narrowing = all(
        item["baseline_bandwidth"] * 0.8 <= item["bandwidth"] <= item["baseline_bandwidth"] * 1.2
        for item in recent_features
    )
    middle_oscillation = _middle_oscillation(recent_features)

    if widening and current_bar.close > middle:
        return _build_signal(
            signal_bar=current_bar,
            source_bars=source_bars,
            direction="buy_watch",
            event_slug="widening-buy",
            strength=0.7,
            confidence=0.78,
            reason=(
                "BOLL 开口且收盘价高于中轨，触发买入观察；"
                f"middle={middle:.4f}, upper={feature['upper']:.4f}, lower={feature['lower']:.4f}, "
                f"bandwidth={bandwidth:.4f}, baseline_bandwidth={baseline:.4f}"
            ),
        )

    if stable_narrowing and current_bar.close < middle and not middle_oscillation:
        return _build_signal(
            signal_bar=current_bar,
            source_bars=source_bars,
            direction="sell_watch",
            event_slug="narrowing-sell",
            strength=0.62,
            confidence=0.72,
            reason=(
                "BOLL 缩口/稳定且收盘价跌破中轨，且无中轨附近震荡，触发卖出观察；"
                f"middle={middle:.4f}, upper={feature['upper']:.4f}, lower={feature['lower']:.4f}, "
                f"bandwidth={bandwidth:.4f}, baseline_bandwidth={baseline:.4f}"
            ),
        )

    if stable_narrowing:
        return _build_signal(
            signal_bar=current_bar,
            source_bars=source_bars,
            direction="observe",
            event_slug="stable-narrowing-observe",
            strength=0.45,
            confidence=0.65,
            reason=(
                "BOLL 缩口/稳定，进入观察状态；"
                f"bandwidth={bandwidth:.4f}, baseline_bandwidth={baseline:.4f}, "
                f"middle_oscillation={middle_oscillation}"
            ),
        )

    return None


def _boll_features(
    bars: list[Bar],
    *,
    window: int,
    baseline_window: int,
    k: float,
) -> list[dict[str, float] | None]:
    features: list[dict[str, float] | None] = []
    raw_features: list[dict[str, float] | None] = []
    for index in range(len(bars)):
        if index + 1 < window:
            raw_features.append(None)
            features.append(None)
            continue
        window_bars = bars[index - window + 1 : index + 1]
        closes = [bar.close for bar in window_bars]
        middle = sum(closes) / window
        sigma = math.sqrt(sum((close - middle) ** 2 for close in closes) / window)
        upper = middle + k * sigma
        lower = middle - k * sigma
        bandwidth = (upper - lower) / middle if middle != 0 else 0.0
        raw_feature = {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "bandwidth": bandwidth,
            "close": bars[index].close,
        }
        raw_features.append(raw_feature)

        recent_raw = [item for item in raw_features if item is not None][-baseline_window - 1 : -1]
        if len(recent_raw) < baseline_window:
            features.append(None)
            continue
        baseline = sum(item["bandwidth"] for item in recent_raw) / baseline_window
        features.append({**raw_feature, "baseline_bandwidth": baseline})
    return features


def _recent_features(
    features: list[dict[str, float] | None],
    index: int,
    *,
    count: int,
) -> list[dict[str, float]]:
    recent = features[max(0, index - count + 1) : index + 1]
    if any(item is None for item in recent):
        return []
    return [item for item in recent if item is not None]


def _middle_oscillation(recent_features: list[dict[str, float]]) -> bool:
    near_middle_count = 0
    for feature in recent_features:
        middle = feature["middle"]
        close = feature["close"]
        if middle != 0 and abs(close - middle) / middle <= 0.005:
            near_middle_count += 1
    return near_middle_count >= 2


def _build_signal(
    *,
    signal_bar: Bar,
    source_bars: list[Bar],
    direction: str,
    event_slug: str,
    strength: float,
    confidence: float,
    reason: str,
) -> Signal:
    signal_id = (
        f"sig-{signal_bar.symbol.lower()}-boll-{event_slug}-"
        f"{signal_bar.timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    )
    return Signal(
        signal_id=signal_id,
        strategy_id=BOLL_STRATEGY_ID,
        symbol=signal_bar.symbol,
        timestamp=signal_bar.timestamp,
        direction=direction,  # type: ignore[arg-type]
        strength=strength,
        confidence=confidence,
        reason=reason,
        trace_id=f"trace-{signal_id}",
        source_bar_ids=[bar.bar_id for bar in source_bars],
        data_quality=_combined_data_quality(source_bars),
        created_at=signal_bar.timestamp,
    )


def _bars_by_symbol(bars: list[Bar]) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[bar.symbol].append(bar)
    return grouped


def _combined_data_quality(bars: list[Bar]) -> str:
    qualities = {bar.quality_flag for bar in bars}
    if qualities == {"normal"}:
        return "normal"
    return ",".join(sorted(qualities))


def _validate_params(window: int, bandwidth_baseline_window: int, k: float) -> None:
    if window <= 0:
        raise ValueError("BOLL window must be positive")
    if bandwidth_baseline_window <= 0:
        raise ValueError("BOLL bandwidth_baseline_window must be positive")
    if k <= 0:
        raise ValueError("BOLL k must be positive")


__all__ = ["BOLL_STRATEGY_ID", "generate_boll_signals"]
