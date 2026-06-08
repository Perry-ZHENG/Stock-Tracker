"""Formal KDJ cross strategy, disabled by default in config."""

from __future__ import annotations

from collections import defaultdict

from stock_agent.schemas import Bar, Signal

KDJ_STRATEGY_ID = "kdj"
DEFAULT_KDJ_PARAMS = (9, 3, 3)


def generate_kdj_signals(
    bars: list[Bar],
    *,
    window: int = 9,
    k_smoothing: int = 3,
    d_smoothing: int = 3,
) -> list[Signal]:
    _validate_params(window, k_smoothing, d_smoothing)
    signals: list[Signal] = []
    warmup = window + 1

    for symbol_bars in _bars_by_symbol(bars).values():
        sorted_bars = sorted(symbol_bars, key=lambda bar: bar.timestamp)
        if len(sorted_bars) < warmup:
            continue
        values = _kdj_values(sorted_bars, window=window, k_smoothing=k_smoothing, d_smoothing=d_smoothing)
        for index in range(window, len(sorted_bars)):
            previous = values[index - 1]
            current = values[index]
            if previous is None or current is None:
                continue
            if previous["k"] <= previous["d"] and current["k"] > current["d"]:
                signals.append(
                    _build_signal(
                        signal_bar=sorted_bars[index],
                        source_bars=sorted_bars[index - window : index + 1],
                        direction="buy_watch",
                        event_slug="golden",
                        window=window,
                        k_smoothing=k_smoothing,
                        d_smoothing=d_smoothing,
                        values=current,
                        reason_prefix=f"KDJ({window},{k_smoothing},{d_smoothing}) K 上穿 D，触发黄金交叉观察提醒",
                    )
                )
            elif previous["k"] >= previous["d"] and current["k"] < current["d"]:
                signals.append(
                    _build_signal(
                        signal_bar=sorted_bars[index],
                        source_bars=sorted_bars[index - window : index + 1],
                        direction="sell_watch",
                        event_slug="death",
                        window=window,
                        k_smoothing=k_smoothing,
                        d_smoothing=d_smoothing,
                        values=current,
                        reason_prefix=f"KDJ({window},{k_smoothing},{d_smoothing}) K 下穿 D，触发死亡交叉观察提醒",
                    )
                )

    return sorted(signals, key=lambda signal: (signal.timestamp, signal.symbol, signal.signal_id))


def calculate_kdj_values(
    bars: list[Bar],
    *,
    window: int = 9,
    k_smoothing: int = 3,
    d_smoothing: int = 3,
) -> list[dict[str, float] | None]:
    _validate_params(window, k_smoothing, d_smoothing)
    return _kdj_values(bars, window=window, k_smoothing=k_smoothing, d_smoothing=d_smoothing)


def _kdj_values(
    bars: list[Bar],
    *,
    window: int,
    k_smoothing: int,
    d_smoothing: int,
) -> list[dict[str, float] | None]:
    values: list[dict[str, float] | None] = []
    previous_k = 50.0
    previous_d = 50.0
    for index in range(len(bars)):
        if index + 1 < window:
            values.append(None)
            continue
        window_bars = bars[index - window + 1 : index + 1]
        highest_high = max(bar.high for bar in window_bars)
        lowest_low = min(bar.low for bar in window_bars)
        if highest_high == lowest_low:
            rsv = 50.0
        else:
            rsv = (bars[index].close - lowest_low) / (highest_high - lowest_low) * 100
        current_k = (1 - 1 / k_smoothing) * previous_k + (1 / k_smoothing) * rsv
        current_d = (1 - 1 / d_smoothing) * previous_d + (1 / d_smoothing) * current_k
        current_j = 3 * current_k - 2 * current_d
        values.append({"rsv": rsv, "k": current_k, "d": current_d, "j": current_j})
        previous_k = current_k
        previous_d = current_d
    return values


def _build_signal(
    *,
    signal_bar: Bar,
    source_bars: list[Bar],
    direction: str,
    event_slug: str,
    window: int,
    k_smoothing: int,
    d_smoothing: int,
    values: dict[str, float],
    reason_prefix: str,
) -> Signal:
    signal_id = (
        f"sig-{signal_bar.symbol.lower()}-kdj-{window}-{k_smoothing}-{d_smoothing}-"
        f"{event_slug}-{signal_bar.timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    )
    return Signal(
        signal_id=signal_id,
        strategy_id=KDJ_STRATEGY_ID,
        symbol=signal_bar.symbol,
        timestamp=signal_bar.timestamp,
        direction=direction,  # type: ignore[arg-type]
        strength=0.6,
        confidence=0.72,
        reason=(
            f"{reason_prefix}；RSV={values['rsv']:.4f}, K={values['k']:.4f}, "
            f"D={values['d']:.4f}, J={values['j']:.4f}"
        ),
        trace_id=f"trace-{signal_id}",
        source_bar_ids=[bar.bar_id for bar in source_bars],
        data_quality=_combined_data_quality(source_bars),
        created_at=signal_bar.timestamp,
    )


def _validate_params(window: int, k_smoothing: int, d_smoothing: int) -> None:
    if window <= 0 or k_smoothing <= 0 or d_smoothing <= 0:
        raise ValueError("KDJ periods must be positive")


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


__all__ = ["DEFAULT_KDJ_PARAMS", "KDJ_STRATEGY_ID", "calculate_kdj_values", "generate_kdj_signals"]
