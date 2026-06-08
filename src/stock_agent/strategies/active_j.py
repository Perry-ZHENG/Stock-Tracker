"""Active J strategy based on KDJ J-line strength.

The first implementation disables support-line detection and falls back to MA80
as the exit reference, matching the T-203 scope.
"""

from __future__ import annotations

from collections import defaultdict

from stock_agent.schemas import Bar, Signal
from stock_agent.strategies.kdj import calculate_kdj_values

ACTIVE_J_STRATEGY_ID = "active_j"
DEFAULT_ACTIVE_J_THRESHOLD = 20.0
DEFAULT_ACTIVE_J_MA_WINDOW = 80
DEFAULT_ACTIVE_J_KDJ_PARAMS = (9, 3, 3)


def generate_active_j_signals(
    bars: list[Bar],
    *,
    j_threshold: float = DEFAULT_ACTIVE_J_THRESHOLD,
    ma_window: int = DEFAULT_ACTIVE_J_MA_WINDOW,
    kdj_window: int = 9,
    k_smoothing: int = 3,
    d_smoothing: int = 3,
) -> list[Signal]:
    _validate_params(j_threshold, ma_window, kdj_window, k_smoothing, d_smoothing)
    signals: list[Signal] = []
    warmup = max(ma_window, kdj_window) + 1

    for symbol_bars in _bars_by_symbol(bars).values():
        sorted_bars = sorted(symbol_bars, key=lambda bar: bar.timestamp)
        if len(sorted_bars) < warmup:
            continue
        kdj_values = calculate_kdj_values(
            sorted_bars,
            window=kdj_window,
            k_smoothing=k_smoothing,
            d_smoothing=d_smoothing,
        )
        for index in range(warmup - 1, len(sorted_bars)):
            current_kdj = kdj_values[index]
            if current_kdj is None:
                continue
            ma_bars = sorted_bars[index - ma_window + 1 : index + 1]
            ma80 = _mean_close(ma_bars)
            if current_kdj["j"] > j_threshold:
                source_bars = sorted_bars[index - warmup + 1 : index + 1]
                signal_bar = sorted_bars[index]
                signals.append(
                    _build_signal(
                        signal_bar=signal_bar,
                        source_bars=source_bars,
                        j_threshold=j_threshold,
                        ma_window=ma_window,
                        kdj_window=kdj_window,
                        k_smoothing=k_smoothing,
                        d_smoothing=d_smoothing,
                        ma_value=ma80,
                        kdj=current_kdj,
                    )
                )

    return sorted(signals, key=lambda signal: (signal.timestamp, signal.symbol, signal.signal_id))


def _build_signal(
    *,
    signal_bar: Bar,
    source_bars: list[Bar],
    j_threshold: float,
    ma_window: int,
    kdj_window: int,
    k_smoothing: int,
    d_smoothing: int,
    ma_value: float,
    kdj: dict[str, float],
) -> Signal:
    signal_id = (
        f"sig-{signal_bar.symbol.lower()}-active-j-"
        f"{signal_bar.timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    )
    return Signal(
        signal_id=signal_id,
        strategy_id=ACTIVE_J_STRATEGY_ID,
        symbol=signal_bar.symbol,
        timestamp=signal_bar.timestamp,
        direction="buy_watch",
        strength=0.68,
        confidence=0.74,
        reason=(
            "活跃策略J：KDJ J线强势，触发买入观察；"
            f"KDJ({kdj_window},{k_smoothing},{d_smoothing}) "
            f"RSV={kdj['rsv']:.4f}, K={kdj['k']:.4f}, D={kdj['d']:.4f}, J={kdj['j']:.4f}; "
            f"J_threshold={j_threshold:.4f}; "
            f"exit_reference=MA{ma_window}; MA{ma_window}={ma_value:.4f}; "
            "support_line=disabled_v1"
        ),
        trace_id=f"trace-{signal_id}",
        source_bar_ids=[bar.bar_id for bar in source_bars],
        data_quality=_combined_data_quality(source_bars),
        created_at=signal_bar.timestamp,
    )


def _validate_params(
    j_threshold: float,
    ma_window: int,
    kdj_window: int,
    k_smoothing: int,
    d_smoothing: int,
) -> None:
    if ma_window <= 0 or kdj_window <= 0 or k_smoothing <= 0 or d_smoothing <= 0:
        raise ValueError("Active J windows and smoothing parameters must be positive")
    if j_threshold < 0:
        raise ValueError("Active J threshold must be non-negative")


def _mean_close(bars: list[Bar]) -> float:
    return sum(bar.close for bar in bars) / len(bars)


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


__all__ = [
    "ACTIVE_J_STRATEGY_ID",
    "DEFAULT_ACTIVE_J_KDJ_PARAMS",
    "DEFAULT_ACTIVE_J_MA_WINDOW",
    "DEFAULT_ACTIVE_J_THRESHOLD",
    "generate_active_j_signals",
]
