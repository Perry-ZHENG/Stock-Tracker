import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.bars.quarantine import persist_quarantine_result, quarantine_abnormal_bars, review_quarantined_bar
from stock_agent.query import QueryService
from stock_agent.schemas import Bar
from stock_agent.storage.repositories import list_abnormal_bars
from stock_agent.storage.sqlite import initialize_database
from stock_agent.strategies.ma_cross_demo import generate_ma_cross_demo_signals
from stock_agent.telegram.listener import handle_telegram_message


class BarQuarantineTests(unittest.TestCase):
    def test_quarantines_bad_prices_duplicate_out_of_order_and_missing_window(self) -> None:
        bars = [
            _bar(0, close=100),
            _bar(1, close=101),
            _bar(1, close=102, source="dup"),
            _bar(3, close=150),
            _bar(2, close=99, source="late"),
            _bar(8, close=100, source="gap"),
        ]
        bad_price = _bar(9, close=0, source="bad").model_copy(update={"open": 0, "high": 1, "low": 0, "close": 0})

        result = quarantine_abnormal_bars([*bars, bad_price], jump_threshold_ratio=0.2)
        reasons = " | ".join(item.reason for item in result.quarantined)

        self.assertTrue(result.has_abnormal)
        self.assertIn("duplicate timestamp", reasons)
        self.assertIn("out-of-order input", reasons)
        self.assertIn("missing window", reasons)
        self.assertIn("price jump", reasons)
        self.assertIn("zero or negative price", reasons)

    def test_quarantined_bars_do_not_enter_strategy_calculation(self) -> None:
        bars = [_bar(0, close=10), _bar(1, close=9), _bar(2, close=8), _bar(3, close=12), _bar(4, close=13)]
        abnormal = _bar(5, close=200)

        result = quarantine_abnormal_bars([*bars, abnormal], jump_threshold_ratio=10.0)
        signals = generate_ma_cross_demo_signals(result.clean_bars)

        self.assertNotIn(abnormal.bar_id, {bar.bar_id for bar in result.clean_bars})
        self.assertEqual(len(signals), 1)
        self.assertNotIn(abnormal.bar_id, signals[0].source_bar_ids)

    def test_quarantine_persists_query_and_manual_review_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_database(root / "data/runtime/stock_agent.sqlite")
            result = quarantine_abnormal_bars([_bar(0, close=100), _bar(1, close=150)], jump_threshold_ratio=0.2)
            persist_quarantine_result(connection, result)
            quarantine_id = list_abnormal_bars(connection)[0]["quarantine_id"]

            review_quarantined_bar(
                connection,
                quarantine_id=quarantine_id,
                status="accepted",
                reviewed_by="cli-admin",
                review_note="accepted after manual CLI review",
            )
            rows = list_abnormal_bars(connection)
            query = QueryService(root).execute("abnormal-bars")
            telegram = handle_telegram_message(
                root=root,
                connection=connection,
                user_id=1,
                text="/abnormal-bars",
                allowed_user_ids=[1],
            )
            connection.close()

        self.assertEqual(rows[0]["status"], "accepted")
        self.assertIn("abnormal_bars_status=ok", query.text)
        self.assertTrue(telegram.ok)
        self.assertIn("abnormal_bars_status=ok", telegram.message)


def _bar(index: int, *, close: float, source: str = "demo") -> Bar:
    timestamp = datetime(2026, 5, 22, 15, 30, tzinfo=UTC) + timedelta(minutes=30 * index)
    return Bar(
        bar_id=f"QQQ-30m-{timestamp.isoformat().replace('+00:00', 'Z')}-{source}",
        symbol="QQQ",
        timestamp=timestamp,
        interval="30m",
        open=close,
        high=close + 1,
        low=max(close - 1, 0),
        close=close,
        volume=1000,
        source=source,
    )


if __name__ == "__main__":
    unittest.main()
