import io
import tempfile
import unittest
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from stock_agent.bars.validation import generate_bar_id
from stock_agent.cli import main
from stock_agent.commands.bars import run_bars_query
from stock_agent.commands.replay import run_replay
from stock_agent.config import DEFAULT_CONFIG, render_config_yaml
from stock_agent.schemas import Bar
from stock_agent.storage.lake import LakeWriter
from stock_agent.storage.repositories import list_signals
from stock_agent.storage.sqlite import open_database


class BarsReplayTests(unittest.TestCase):
    def test_bars_query_filters_lake_rows_by_symbol_and_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("stock_agent.storage.lake._parquet_available", return_value=False):
            root = Path(tmp_dir)
            _write_config(root)
            LakeWriter(root / "data/lake").write_raw_bars(
                [
                    *_bars_from_closes([10, 10, 10, 20], symbol="QQQ"),
                    *_bars_from_closes([30], symbol="SPY"),
                ]
            )
            stream = io.StringIO()

            result = run_bars_query(
                root,
                symbol="QQQ",
                from_value="2026-05-22T14:00:00Z",
                to_value="2026-05-22T15:00:00Z",
                stream=stream,
            )

        self.assertTrue(result.ok)
        self.assertEqual([bar.timestamp.hour for bar in result.bars], [14, 14, 15])
        self.assertIn("timestamp | symbol | interval", stream.getvalue())
        self.assertIn("QQQ", stream.getvalue())
        self.assertNotIn("SPY", stream.getvalue())

    def test_replay_is_deterministic_and_dry_run_does_not_persist_signals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("stock_agent.storage.lake._parquet_available", return_value=False):
            root = Path(tmp_dir)
            _write_config(root)
            LakeWriter(root / "data/lake").write_raw_bars(_bars_from_closes([10, 10, 10, 20]))
            first_stream = io.StringIO()
            second_stream = io.StringIO()

            first = run_replay(
                root,
                from_value="2026-05-22",
                to_value="2026-05-22",
                symbols=["QQQ"],
                stream=first_stream,
            )
            second = run_replay(
                root,
                from_value="2026-05-22",
                to_value="2026-05-22",
                symbols=["QQQ"],
                stream=second_stream,
            )

        self.assertTrue(first.ok)
        self.assertEqual([signal.signal_id for signal in first.signals], [signal.signal_id for signal in second.signals])
        self.assertEqual(len(first.signals), 1)
        self.assertFalse((root / "data/runtime/stock_agent.sqlite").exists())
        self.assertIn("dry_run=true", first_stream.getvalue())
        self.assertEqual(first_stream.getvalue(), second_stream.getvalue())

    def test_replay_persist_writes_signals_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("stock_agent.storage.lake._parquet_available", return_value=False):
            root = Path(tmp_dir)
            _write_config(root)
            LakeWriter(root / "data/lake").write_raw_bars(_bars_from_closes([10, 10, 10, 20]))

            result = run_replay(
                root,
                from_value="2026-05-22",
                to_value="2026-05-22",
                symbols=["QQQ"],
                persist=True,
                stream=io.StringIO(),
            )
            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            signals = list_signals(connection)
            connection.close()

        self.assertTrue(result.persisted)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0].signal_id, result.signals[0].signal_id)

    def test_cli_bars_and_replay_use_real_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch("stock_agent.storage.lake._parquet_available", return_value=False):
            root = Path(tmp_dir)
            _write_config(root)
            LakeWriter(root / "data/lake").write_raw_bars(_bars_from_closes([10, 10, 10, 20]))

            with patch("pathlib.Path.cwd", return_value=root):
                bars_exit = main(["cli", "bars", "--symbol", "QQQ", "--from", "2026-05-22", "--to", "2026-05-22"])
                replay_exit = main(["replay", "--symbols", "QQQ", "--from", "2026-05-22", "--to", "2026-05-22"])

        self.assertEqual(bars_exit, 0)
        self.assertEqual(replay_exit, 0)


def _write_config(root: Path) -> None:
    config = deepcopy(DEFAULT_CONFIG)
    config["symbols"]["default"] = ["QQQ"]
    config["strategies"]["ma_cross"]["enabled"] = True
    config["strategies"]["ma_cross"]["pairs"] = [[2, 3]]
    config["strategies"]["boll"]["enabled"] = False
    config["strategies"]["macd"]["enabled"] = False
    config["strategies"]["kdj"]["enabled"] = False
    config["strategies"]["active_j"]["enabled"] = False
    config_path = root / "configs" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(render_config_yaml(config), encoding="utf-8")


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
