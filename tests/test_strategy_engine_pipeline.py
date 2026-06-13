import copy
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.bars.validation import generate_bar_id
from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.schemas import Bar
from stock_agent.signals import SignalPipeline
from stock_agent.storage.repositories import list_strategy_snapshots, list_trace_chain
from stock_agent.storage.sqlite import initialize_database
from stock_agent.strategies import StrategyEngine


class StrategyEnginePipelineTests(unittest.TestCase):
    def test_engine_runs_only_enabled_strategies(self) -> None:
        config = _config(
            ma_enabled=True,
            boll_enabled=False,
            ma_pairs=[[3, 5]],
        )
        bars = _bars_from_closes([10, 10, 10, 10, 10, 20])

        result = StrategyEngine(config.strategies).run(bars)

        self.assertEqual(result.enabled_strategies, ["ma_cross"])
        self.assertEqual(len(result.signals), 1)
        self.assertEqual(result.signals[0].strategy_id, "ma_cross")
        self.assertEqual(result.traces, [])

    def test_engine_records_skipped_trace_when_warmup_is_insufficient(self) -> None:
        config = _config(
            ma_enabled=True,
            boll_enabled=False,
            ma_pairs=[[3, 5]],
        )
        bars = _bars_from_closes([10, 10, 10])

        result = StrategyEngine(config.strategies).run(bars)

        self.assertEqual(result.signals, [])
        self.assertEqual(len(result.traces), 1)
        self.assertEqual(result.traces[0].status, "skipped")
        self.assertIn("insufficient warm-up", result.traces[0].error_msg or "")

    def test_pipeline_persists_snapshot_and_signal_traces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            config = _config(
                ma_enabled=True,
                boll_enabled=False,
                ma_pairs=[[3, 5]],
            )
            bars = _bars_from_closes([10, 10, 10, 10, 10, 20])

            result = SignalPipeline(config=config, connection=connection).run(bars)
            snapshots = list_strategy_snapshots(connection)
            traces = list_trace_chain(connection)

        self.assertEqual(len(result.signals), 1)
        self.assertEqual(result.snapshot.enabled_strategies, ["ma_cross"])
        self.assertEqual(result.snapshot.strategy_params["ma_cross"]["pairs"], [[3, 5]])
        self.assertEqual(snapshots, [result.snapshot])
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].trace_id, result.signals[0].trace_id)
        self.assertEqual(traces[0].input_ref, result.signals[0].source_bar_ids)

    def test_pipeline_keeps_multi_strategy_raw_signals_independent(self) -> None:
        config = _config(
            ma_enabled=True,
            boll_enabled=True,
            ma_pairs=[[3, 5]],
            boll_window=3,
            boll_baseline=2,
        )
        bars = _bars_from_closes([10, 10, 10, 10, 10, 8, 14])

        result = SignalPipeline(config=config).run(bars)

        strategy_ids = {signal.strategy_id for signal in result.signals}
        self.assertEqual(strategy_ids, {"ma_cross", "boll"})
        self.assertEqual(len({signal.signal_id for signal in result.signals}), len(result.signals))
        self.assertEqual(len(result.traces), len(result.signals))


def _config(
    *,
    ma_enabled: bool,
    boll_enabled: bool,
    ma_pairs: list[list[int]],
    boll_window: int = 20,
    boll_baseline: int = 20,
):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["strategies"]["ma_cross"]["enabled"] = ma_enabled
    config["strategies"]["ma_cross"]["pairs"] = ma_pairs
    config["strategies"]["boll"]["enabled"] = boll_enabled
    config["strategies"]["boll"]["window"] = boll_window
    config["strategies"]["boll"]["bandwidth_baseline_window"] = boll_baseline
    config["strategies"]["macd"]["enabled"] = False
    config["strategies"]["kdj"]["enabled"] = False
    config["strategies"]["active_j"]["enabled"] = False
    return validate_config(config)


def _bars_from_closes(closes: list[float], *, symbol: str = "QQQ") -> list[Bar]:
    start = datetime(2026, 5, 22, 13, 30, tzinfo=UTC)
    bars: list[Bar] = []
    for index, close in enumerate(closes):
        timestamp = start + timedelta(minutes=30 * index)
        timestamp_text = timestamp.isoformat().replace("+00:00", "Z")
        bars.append(
            Bar(
                bar_id=generate_bar_id(symbol, "30m", timestamp_text, "unit_test"),
                symbol=symbol,
                timestamp=timestamp,
                interval="30m",
                open=close,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000 + index,
                vwap=close,
                source="unit_test",
                quality_flag="normal",
            )
        )
    return bars


if __name__ == "__main__":
    unittest.main()
