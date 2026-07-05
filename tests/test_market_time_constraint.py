import unittest

from stock_agent.dialog.intents import ClarificationIntent, ReadOnlyIntent
from stock_agent.dialog.natural_language import parse_natural_language_command
from stock_agent.dialog.time_window import normalize_explicit_time_window


class MarketTimeConstraintTests(unittest.TestCase):
    def test_normalizes_named_market_timezone_to_utc(self) -> None:
        start, end = normalize_explicit_time_window(
            from_ts="2026-07-03 09:30",
            to_ts="2026-07-03 16:00",
            timezone_name="America/New_York",
        )

        self.assertEqual(start, "2026-07-03T13:30:00Z")
        self.assertEqual(end, "2026-07-03T20:00:00Z")

    def test_rejects_date_only_or_unknown_timezone(self) -> None:
        with self.assertRaisesRegex(ValueError, "clock time"):
            normalize_explicit_time_window(
                from_ts="2026-07-03",
                to_ts="2026-07-04",
                timezone_name="America/New_York",
            )
        with self.assertRaisesRegex(ValueError, "IANA timezone"):
            normalize_explicit_time_window(
                from_ts="2026-07-03 09:30",
                to_ts="2026-07-03 16:00",
                timezone_name="New York",
            )

    def test_natural_symbol_query_without_time_asks_user(self) -> None:
        intent = parse_natural_language_command("查询 QQQ 的信号")

        self.assertIsInstance(intent, ClarificationIntent)
        assert isinstance(intent, ClarificationIntent)
        self.assertIn("开始时间", intent.question)
        self.assertIn("结束时间", intent.question)
        self.assertIn("IANA 时区", intent.question)

    def test_natural_symbol_query_accepts_complete_time_window(self) -> None:
        intent = parse_natural_language_command(
            "查询 QQQ 从 2026-07-03 09:30 到 2026-07-03 16:00 "
            "的信号，America/New_York"
        )

        self.assertIsInstance(intent, ReadOnlyIntent)
        assert isinstance(intent, ReadOnlyIntent)
        self.assertEqual(intent.symbol, "QQQ")
        self.assertEqual(intent.from_ts, "2026-07-03T13:30:00Z")
        self.assertEqual(intent.to_ts, "2026-07-03T20:00:00Z")


if __name__ == "__main__":
    unittest.main()
