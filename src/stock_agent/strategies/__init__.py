"""Strategy implementations."""

from stock_agent.strategies.boll import BOLL_STRATEGY_ID, generate_boll_signals
from stock_agent.strategies.ma_cross import (
    DEFAULT_MA_CROSS_PAIRS,
    MA_CROSS_STRATEGY_ID,
    generate_ma_cross_signals,
)
from stock_agent.strategies.ma_cross_demo import MA_CROSS_DEMO_STRATEGY_ID, generate_ma_cross_demo_signals

__all__ = [
    "BOLL_STRATEGY_ID",
    "DEFAULT_MA_CROSS_PAIRS",
    "MA_CROSS_DEMO_STRATEGY_ID",
    "MA_CROSS_STRATEGY_ID",
    "generate_boll_signals",
    "generate_ma_cross_demo_signals",
    "generate_ma_cross_signals",
]
