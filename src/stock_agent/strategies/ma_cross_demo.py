"""Demo MA2/MA3 cross strategy used only for deterministic regression tests."""

from __future__ import annotations

from stock_agent.schemas import Bar, Signal

MA_CROSS_DEMO_STRATEGY_ID = "ma_cross_demo_2_3"


def generate_ma_cross_demo_signals(bars: list[Bar]) -> list[Signal]:
    sorted_bars = sorted(bars, key=lambda bar: bar.timestamp)
    signals: list[Signal] = []

    for current_index in range(3, len(sorted_bars)):
        previous_window = sorted_bars[current_index - 3 : current_index]
        current_window = sorted_bars[current_index - 2 : current_index + 1]

        previous_ma2 = _mean_close(previous_window[-2:])
        previous_ma3 = _mean_close(previous_window)
        current_ma2 = _mean_close(current_window[-2:])
        current_ma3 = _mean_close(current_window)

        if previous_ma2 <= previous_ma3 and current_ma2 > current_ma3:
            signal_bar = sorted_bars[current_index]
            source_bars = current_window
            signal_id = f"sig-{signal_bar.symbol.lower()}-ma2-ma3-{_compact_timestamp(signal_bar)}"
            signals.append(
                Signal(
                    signal_id=signal_id,
                    strategy_id=MA_CROSS_DEMO_STRATEGY_ID,
                    symbol=signal_bar.symbol,
                    timestamp=signal_bar.timestamp,
                    direction="buy_watch",
                    strength=0.7,
                    confidence=0.9,
                    reason="MA2 上穿 MA3，触发黄金交叉观察提醒",
                    trace_id=f"trace-{signal_id}",
                    source_bar_ids=[bar.bar_id for bar in source_bars],
                    data_quality=_combined_data_quality(source_bars),
                    created_at=signal_bar.timestamp,
                )
            )
    return signals


def _mean_close(bars: list[Bar]) -> float:
    return sum(bar.close for bar in bars) / len(bars)


def _compact_timestamp(bar: Bar) -> str:
    return bar.timestamp.strftime("%Y%m%dT%H%M%SZ")


def _combined_data_quality(bars: list[Bar]) -> str:
    qualities = {bar.quality_flag for bar in bars}
    if qualities == {"normal"}:
        return "normal"
    return ",".join(sorted(qualities))
