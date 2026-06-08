"""Formal MACD cross strategy, disabled by default in config."""

from __future__ import annotations

from collections import defaultdict

from stock_agent.schemas import Bar, Signal

MACD_STRATEGY_ID = "macd"
DEFAULT_MACD_PARAMS = (12, 26, 9)


def generate_macd_signals(
    bars: list[Bar],
    *,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> list[Signal]:
    _validate_params(fast, slow, signal_period)
    signals: list[Signal] = []
    warmup = slow + signal_period + 1

    for symbol_bars in _bars_by_symbol(bars).values():
        sorted_bars = sorted(symbol_bars, key=lambda bar: bar.timestamp)
        if len(sorted_bars) < warmup:
            continue
        values = _macd_values(sorted_bars, fast=fast, slow=slow, signal_period=signal_period)
        for index in range(warmup - 1, len(sorted_bars)):
            previous = values[index - 1]
            current = values[index]
            if previous["dif"] <= previous["dea"] and current["dif"] > current["dea"]:
                signals.append(
                    _build_signal(
                        signal_bar=sorted_bars[index],
                        source_bars=sorted_bars[index - warmup + 1 : index + 1],
                        direction="buy_watch",
                        event_slug="golden",
                        fast=fast,
                        slow=slow,
                        signal_period=signal_period,
                        values=current,
                        reason_prefix=f"MACD({fast},{slow},{signal_period}) DIF 上穿 DEA，触发黄金交叉观察提醒",
                    )
                )
            elif previous["dif"] >= previous["dea"] and current["dif"] < current["dea"]:
                signals.append(
                    _build_signal(
                        signal_bar=sorted_bars[index],
                        source_bars=sorted_bars[index - warmup + 1 : index + 1],
                        direction="sell_watch",
                        event_slug="death",
                        fast=fast,
                        slow=slow,
                        signal_period=signal_period,
                        values=current,
                        reason_prefix=f"MACD({fast},{slow},{signal_period}) DIF 下穿 DEA，触发死亡交叉观察提醒",
                    )
                )

    return sorted(signals, key=lambda signal: (signal.timestamp, signal.symbol, signal.signal_id))


def _macd_values(
    bars: list[Bar],
    *,
    fast: int,
    slow: int,
    signal_period: int,
) -> list[dict[str, float]]:
    closes = [bar.close for bar in bars]
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [fast_value - slow_value for fast_value, slow_value in zip(ema_fast, ema_slow, strict=True)]
    dea = _ema(dif, signal_period)
    return [
        {"dif": dif_value, "dea": dea_value, "macd": 2 * (dif_value - dea_value)}
        for dif_value, dea_value in zip(dif, dea, strict=True)
    ]


def _ema(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    ema_values: list[float] = []
    for value in values:
        if not ema_values:
            ema_values.append(value)
        else:
            ema_values.append(alpha * value + (1 - alpha) * ema_values[-1])
    return ema_values


def _build_signal(
    *,
    signal_bar: Bar,
    source_bars: list[Bar],
    direction: str,
    event_slug: str,
    fast: int,
    slow: int,
    signal_period: int,
    values: dict[str, float],
    reason_prefix: str,
) -> Signal:
    signal_id = (
        f"sig-{signal_bar.symbol.lower()}-macd-{fast}-{slow}-{signal_period}-"
        f"{event_slug}-{signal_bar.timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    )
    return Signal(
        signal_id=signal_id,
        strategy_id=MACD_STRATEGY_ID,
        symbol=signal_bar.symbol,
        timestamp=signal_bar.timestamp,
        direction=direction,  # type: ignore[arg-type]
        strength=0.66,
        confidence=0.78,
        reason=(
            f"{reason_prefix}；DIF={values['dif']:.4f}, DEA={values['dea']:.4f}, "
            f"MACD={values['macd']:.4f}"
        ),
        trace_id=f"trace-{signal_id}",
        source_bar_ids=[bar.bar_id for bar in source_bars],
        data_quality=_combined_data_quality(source_bars),
        created_at=signal_bar.timestamp,
    )


def _validate_params(fast: int, slow: int, signal_period: int) -> None:
    if fast <= 0 or slow <= 0 or signal_period <= 0:
        raise ValueError("MACD periods must be positive")
    if fast >= slow:
        raise ValueError("MACD fast period must be smaller than slow period")


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


__all__ = ["DEFAULT_MACD_PARAMS", "MACD_STRATEGY_ID", "generate_macd_signals"]
