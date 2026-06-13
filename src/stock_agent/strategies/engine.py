"""Unified strategy engine for configured formal strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stock_agent.config import StrategiesConfig
from stock_agent.schemas import Bar, Signal, TraceChain
from stock_agent.strategies.active_j import ACTIVE_J_STRATEGY_ID, generate_active_j_signals
from stock_agent.strategies.boll import BOLL_STRATEGY_ID, generate_boll_signals
from stock_agent.strategies.kdj import KDJ_STRATEGY_ID, generate_kdj_signals
from stock_agent.strategies.macd import MACD_STRATEGY_ID, generate_macd_signals
from stock_agent.strategies.ma_cross import MA_CROSS_STRATEGY_ID, generate_ma_cross_signals
from stock_agent.tracing import skipped_trace


@dataclass(frozen=True)
class StrategyRunResult:
    signals: list[Signal]
    traces: list[TraceChain]
    enabled_strategies: list[str]
    strategy_params: dict[str, dict[str, Any]]


class StrategyEngine:
    """Run enabled strategies against prepared regular-session bars."""

    def __init__(self, config: StrategiesConfig) -> None:
        self.config = config

    def run(self, bars: list[Bar]) -> StrategyRunResult:
        signals: list[Signal] = []
        traces: list[TraceChain] = []
        enabled = self.enabled_strategy_ids()
        params = self.strategy_params()

        for strategy_id in enabled:
            warmup = self.required_warmup(strategy_id)
            if _max_symbol_bars(bars) < warmup:
                traces.append(
                    skipped_trace(
                        trace_id=f"trace-{strategy_id}-warmup-skipped",
                        module="strategy_engine",
                        input_ref={"strategy_id": strategy_id, "bar_count": len(bars), "required_warmup": warmup},
                        reason=f"insufficient warm-up bars for {strategy_id}: required {warmup}",
                    )
                )
                continue
            signals.extend(self._run_strategy(strategy_id, bars))

        return StrategyRunResult(
            signals=sorted(signals, key=lambda signal: (signal.timestamp, signal.symbol, signal.signal_id)),
            traces=traces,
            enabled_strategies=enabled,
            strategy_params=params,
        )

    def enabled_strategy_ids(self) -> list[str]:
        enabled: list[str] = []
        if self.config.ma_cross.enabled:
            enabled.append(MA_CROSS_STRATEGY_ID)
        if self.config.boll.enabled:
            enabled.append(BOLL_STRATEGY_ID)
        if self.config.macd.enabled:
            enabled.append(MACD_STRATEGY_ID)
        if self.config.kdj.enabled:
            enabled.append(KDJ_STRATEGY_ID)
        if self.config.active_j.enabled:
            enabled.append(ACTIVE_J_STRATEGY_ID)
        return enabled

    def strategy_params(self) -> dict[str, dict[str, Any]]:
        params: dict[str, dict[str, Any]] = {}
        if self.config.ma_cross.enabled:
            params[MA_CROSS_STRATEGY_ID] = {"pairs": [list(pair) for pair in self.config.ma_cross.pairs]}
        if self.config.boll.enabled:
            params[BOLL_STRATEGY_ID] = {
                "window": self.config.boll.window,
                "bandwidth_baseline_window": self.config.boll.bandwidth_baseline_window,
            }
        if self.config.macd.enabled:
            params[MACD_STRATEGY_ID] = {
                "fast": self.config.macd.fast,
                "slow": self.config.macd.slow,
                "signal": self.config.macd.signal,
            }
        if self.config.kdj.enabled:
            params[KDJ_STRATEGY_ID] = {
                "window": self.config.kdj.window,
                "k_smoothing": self.config.kdj.k_smoothing,
                "d_smoothing": self.config.kdj.d_smoothing,
            }
        if self.config.active_j.enabled:
            params[ACTIVE_J_STRATEGY_ID] = {
                "j_threshold": self.config.active_j.j_threshold,
                "ma_window": self.config.active_j.ma_window,
                "kdj_window": self.config.active_j.kdj_window,
                "k_smoothing": self.config.active_j.k_smoothing,
                "d_smoothing": self.config.active_j.d_smoothing,
            }
        return params

    def required_warmup(self, strategy_id: str) -> int:
        if strategy_id == MA_CROSS_STRATEGY_ID:
            return max(long for _short, long in self.config.ma_cross.pairs) + 1
        if strategy_id == BOLL_STRATEGY_ID:
            return self.config.boll.window + self.config.boll.bandwidth_baseline_window + 2
        if strategy_id == MACD_STRATEGY_ID:
            return self.config.macd.slow + self.config.macd.signal + 1
        if strategy_id == KDJ_STRATEGY_ID:
            return self.config.kdj.window + 1
        if strategy_id == ACTIVE_J_STRATEGY_ID:
            return max(self.config.active_j.ma_window, self.config.active_j.kdj_window) + 1
        raise ValueError(f"unsupported strategy id: {strategy_id}")

    def _run_strategy(self, strategy_id: str, bars: list[Bar]) -> list[Signal]:
        if strategy_id == MA_CROSS_STRATEGY_ID:
            return generate_ma_cross_signals(bars, pairs=self.config.ma_cross.pairs)
        if strategy_id == BOLL_STRATEGY_ID:
            return generate_boll_signals(
                bars,
                window=self.config.boll.window,
                bandwidth_baseline_window=self.config.boll.bandwidth_baseline_window,
            )
        if strategy_id == MACD_STRATEGY_ID:
            return generate_macd_signals(
                bars,
                fast=self.config.macd.fast,
                slow=self.config.macd.slow,
                signal_period=self.config.macd.signal,
            )
        if strategy_id == KDJ_STRATEGY_ID:
            return generate_kdj_signals(
                bars,
                window=self.config.kdj.window,
                k_smoothing=self.config.kdj.k_smoothing,
                d_smoothing=self.config.kdj.d_smoothing,
            )
        if strategy_id == ACTIVE_J_STRATEGY_ID:
            return generate_active_j_signals(
                bars,
                j_threshold=self.config.active_j.j_threshold,
                ma_window=self.config.active_j.ma_window,
                kdj_window=self.config.active_j.kdj_window,
                k_smoothing=self.config.active_j.k_smoothing,
                d_smoothing=self.config.active_j.d_smoothing,
            )
        raise ValueError(f"unsupported strategy id: {strategy_id}")


def _max_symbol_bars(bars: list[Bar]) -> int:
    counts: dict[str, int] = {}
    for bar in bars:
        counts[bar.symbol] = counts.get(bar.symbol, 0) + 1
    return max(counts.values(), default=0)


__all__ = ["StrategyEngine", "StrategyRunResult"]
