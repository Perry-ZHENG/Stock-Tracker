"""Strategy implementations."""

from stock_agent.strategies.active_j import (
    ACTIVE_J_STRATEGY_ID,
    DEFAULT_ACTIVE_J_KDJ_PARAMS,
    DEFAULT_ACTIVE_J_MA_WINDOW,
    DEFAULT_ACTIVE_J_THRESHOLD,
    generate_active_j_signals,
)
from stock_agent.strategies.boll import BOLL_STRATEGY_ID, generate_boll_signals
from stock_agent.strategies.engine import StrategyEngine, StrategyRunResult
from stock_agent.strategies.kdj import (
    DEFAULT_KDJ_PARAMS,
    KDJ_STRATEGY_ID,
    calculate_kdj_values,
    generate_kdj_signals,
)
from stock_agent.strategies.macd import DEFAULT_MACD_PARAMS, MACD_STRATEGY_ID, generate_macd_signals
from stock_agent.strategies.ma_cross import (
    DEFAULT_MA_CROSS_PAIRS,
    MA_CROSS_STRATEGY_ID,
    generate_ma_cross_signals,
)
from stock_agent.strategies.ma_cross_demo import MA_CROSS_DEMO_STRATEGY_ID, generate_ma_cross_demo_signals

__all__ = [
    "ACTIVE_J_STRATEGY_ID",
    "BOLL_STRATEGY_ID",
    "DEFAULT_ACTIVE_J_KDJ_PARAMS",
    "DEFAULT_ACTIVE_J_MA_WINDOW",
    "DEFAULT_ACTIVE_J_THRESHOLD",
    "DEFAULT_KDJ_PARAMS",
    "DEFAULT_MACD_PARAMS",
    "DEFAULT_MA_CROSS_PAIRS",
    "KDJ_STRATEGY_ID",
    "MA_CROSS_DEMO_STRATEGY_ID",
    "MA_CROSS_STRATEGY_ID",
    "MACD_STRATEGY_ID",
    "StrategyEngine",
    "StrategyRunResult",
    "generate_active_j_signals",
    "generate_boll_signals",
    "calculate_kdj_values",
    "generate_kdj_signals",
    "generate_macd_signals",
    "generate_ma_cross_demo_signals",
    "generate_ma_cross_signals",
]
