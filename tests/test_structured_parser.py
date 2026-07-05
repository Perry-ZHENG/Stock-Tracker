import unittest

from stock_agent.dialog.intents import ClarificationIntent, HighRiskBlockedIntent, PendingChangeIntent, ReadOnlyIntent
from stock_agent.dialog.parser import parse_structured_command


class StructuredParserTests(unittest.TestCase):
    def test_parses_common_signal_query_with_explicit_time_without_llm(self) -> None:
        intent = parse_structured_command(
            "show signals NVDA from 2026-07-03T09:30:00-04:00 "
            "to 2026-07-03T16:00:00-04:00 timezone America/New_York limit 5",
            source="cli",
        )

        self.assertIsInstance(intent, ReadOnlyIntent)
        self.assertEqual(intent.query, "signals")
        self.assertEqual(intent.symbol, "NVDA")
        self.assertEqual(intent.limit, 5)
        self.assertEqual(intent.source, "cli")
        self.assertEqual(intent.from_ts, "2026-07-03T13:30:00Z")
        self.assertEqual(intent.to_ts, "2026-07-03T20:00:00Z")

    def test_symbol_query_without_time_requires_clarification(self) -> None:
        signal_intent = parse_structured_command("最近 NVDA 有什么信号", source="cli")
        change_intent = parse_structured_command("添加 QQQ 到关注", source="cli")

        self.assertIsInstance(signal_intent, ClarificationIntent)
        self.assertIn("IANA 时区", signal_intent.question)
        self.assertIsInstance(change_intent, PendingChangeIntent)
        self.assertEqual(change_intent.action, "add_symbol")
        self.assertEqual(change_intent.symbol, "QQQ")

    def test_parses_bars_trace_stats_news_and_schedule_queries(self) -> None:
        cases = [
            (
                "bars QQQ from 2026-07-03T09:30:00-04:00 "
                "to 2026-07-03T16:00:00-04:00 timezone America/New_York",
                "bars",
            ),
            ("trace sig-001", "trace"),
            ("stats month", "stats"),
            ("news qqq limit 3", "news"),
            ("schedule", "schedule"),
            ("health", "health"),
        ]

        for text, query in cases:
            with self.subTest(text=text):
                intent = parse_structured_command(text)
                self.assertIsInstance(intent, ReadOnlyIntent)
                self.assertEqual(intent.query, query)

    def test_parses_pending_change_commands_for_telegram_review_flow(self) -> None:
        cases = [
            ("add symbol qqq", "add_symbol"),
            ("remove symbol msft", "remove_symbol"),
            ("enable strategy macd", "enable_strategy"),
            ("disable strategy boll", "disable_strategy"),
            ("change watch window 09:30 16:00", "change_watch_window"),
        ]

        for text, action in cases:
            with self.subTest(text=text):
                intent = parse_structured_command(text, source="telegram")
                self.assertIsInstance(intent, PendingChangeIntent)
                self.assertEqual(intent.action, action)
                self.assertEqual(intent.risk, "pending_change")
                self.assertEqual(intent.source, "telegram")

    def test_high_risk_commands_are_blocked_before_business_handlers(self) -> None:
        for text in ["buy 10 shares of QQQ", "cancel order 123", "withdraw 1000"]:
            with self.subTest(text=text):
                intent = parse_structured_command(text)

                self.assertIsInstance(intent, HighRiskBlockedIntent)
                self.assertFalse(intent.executable)

    def test_unknown_or_empty_command_returns_readable_clarification(self) -> None:
        for text in ["", "do something with tech stocks"]:
            with self.subTest(text=text):
                intent = parse_structured_command(text)

                self.assertIsInstance(intent, ClarificationIntent)
                self.assertFalse(intent.executable)
                self.assertGreater(len(intent.candidates), 0)


if __name__ == "__main__":
    unittest.main()
