"""Formal moving-average cross strategy for v1 watch signals."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from stock_agent.schemas import Bar, Signal

MA_CROSS_STRATEGY_ID = "ma_cross"
DEFAULT_MA_CROSS_PAIRS: tuple[tuple[int, int], ...] = ((3, 5), (5, 10), (10, 20))


def generate_ma_cross_signals(
    bars: list[Bar],
    *,
    pairs: Iterable[tuple[int, int]] = DEFAULT_MA_CROSS_PAIRS,
) -> list[Signal]:
    normalized_pairs = _normalize_pairs(pairs)
    signals: list[Signal] = []

    for symbol_bars in _bars_by_symbol(bars).values():
        sorted_bars = sorted(symbol_bars, key=lambda bar: bar.timestamp)
        for short_window, long_window in normalized_pairs:
            signals.extend(_generate_pair_signals(sorted_bars, short_window, long_window))

    return sorted(signals, key=lambda signal: (signal.timestamp, signal.symbol, signal.signal_id))


def _generate_pair_signals(
    bars: list[Bar],
    short_window: int,
    long_window: int,
) -> list[Signal]:
    signals: list[Signal] = []

    # Need one previous full long window plus the current bar to compare two MA states.
    for current_index in range(long_window, len(bars)):
        previous_long_bars = bars[current_index - long_window : current_index]
        current_long_bars = bars[current_index - long_window + 1 : current_index + 1]
        previous_short_bars = previous_long_bars[-short_window:]
        current_short_bars = current_long_bars[-short_window:]

        previous_short_ma = _mean_close(previous_short_bars)
        previous_long_ma = _mean_close(previous_long_bars)
        current_short_ma = _mean_close(current_short_bars)
        current_long_ma = _mean_close(current_long_bars)

        if previous_short_ma <= previous_long_ma and current_short_ma > current_long_ma:
            signals.append(
                _build_signal(
                    signal_bar=bars[current_index],
                    source_bars=bars[current_index - long_window : current_index + 1],
                    short_window=short_window,
                    long_window=long_window,
                    direction="buy_watch",
                    reason=(
                        f"MA{short_window} 上穿 MA{long_window}，触发黄金交叉观察提醒；"
                        f"prev_short={previous_short_ma:.4f}, prev_long={previous_long_ma:.4f}, "
                        f"current_short={current_short_ma:.4f}, current_long={current_long_ma:.4f}"
                    ),
                )
            )
        elif previous_short_ma >= previous_long_ma and current_short_ma < current_long_ma:
            signals.append(
                _build_signal(
                    signal_bar=bars[current_index],
                    source_bars=bars[current_index - long_window : current_index + 1],
                    short_window=short_window,
                    long_window=long_window,
                    direction="sell_watch",
                    reason=(
                        f"MA{short_window} 下穿 MA{long_window}，触发死亡交叉观察提醒；"
                        f"prev_short={previous_short_ma:.4f}, prev_long={previous_long_ma:.4f}, "
                        f"current_short={current_short_ma:.4f}, current_long={current_long_ma:.4f}"
                    ),
                )
            )

    return signals


def _build_signal(
    *,
    signal_bar: Bar,
    source_bars: list[Bar],
    short_window: int,
    long_window: int,
    direction: str,
    reason: str,
) -> Signal:
    direction_slug = "golden" if direction == "buy_watch" else "death"
    signal_id = (
        f"sig-{signal_bar.symbol.lower()}-ma{short_window}-ma{long_window}-"
        f"{direction_slug}-{signal_bar.timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    )
    return Signal(
        signal_id=signal_id,
        strategy_id=MA_CROSS_STRATEGY_ID,
        symbol=signal_bar.symbol,
        timestamp=signal_bar.timestamp,
        direction=direction,  # type: ignore[arg-type]
        strength=0.65,
        confidence=0.8,
        reason=reason,
        trace_id=f"trace-{signal_id}",
        source_bar_ids=[bar.bar_id for bar in source_bars],
        data_quality=_combined_data_quality(source_bars),
        created_at=signal_bar.timestamp,
    )


def _normalize_pairs(pairs: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    normalized = tuple(pairs)
    for short_window, long_window in normalized:
        if short_window <= 0 or long_window <= 0:
            raise ValueError("MA windows must be positive")
        if short_window >= long_window:
            raise ValueError("MA short window must be smaller than long window")
    return normalized


def _bars_by_symbol(bars: list[Bar]) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[bar.symbol].append(bar)
    return grouped


def _mean_close(bars: list[Bar]) -> float:
    return sum(bar.close for bar in bars) / len(bars)


def _combined_data_quality(bars: list[Bar]) -> str:
    qualities = {bar.quality_flag for bar in bars}
    if qualities == {"normal"}:
        return "normal"
    return ",".join(sorted(qualities))


__all__ = [
    "DEFAULT_MA_CROSS_PAIRS",
    "MA_CROSS_STRATEGY_ID",
    "generate_ma_cross_signals",
]
