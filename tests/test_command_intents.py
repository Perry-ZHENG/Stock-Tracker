import unittest

from pydantic import ValidationError

from stock_agent.dialog.intents import (
    ClarificationIntent,
    HighRiskBlockedIntent,
    PendingChangeIntent,
    ReadOnlyIntent,
    intent_json_schema,
    validate_intent,
)


class CommandIntentTests(unittest.TestCase):
    def test_read_only_query_intent_supports_all_query_names(self) -> None:
        for query in ["signals", "health", "bars", "news", "stats", "trace", "schedule"]:
            with self.subTest(query=query):
                payload = {
                    "intent_type": "read_only",
                    "query": query,
                    "symbol": "qqq",
                    "limit": 5,
                }
                if query in {"signals", "bars"}:
                    payload.update(
                        {
                            "from_ts": "2026-07-03 09:30",
                            "to_ts": "2026-07-03 16:00",
                            "timezone": "America/New_York",
                        }
                    )
                intent = validate_intent(
                    payload
                )

                self.assertIsInstance(intent, ReadOnlyIntent)
                self.assertEqual(intent.risk, "read_only")
                self.assertTrue(intent.executable)
                self.assertEqual(intent.symbol, "QQQ")

    def test_symbol_market_query_rejects_missing_explicit_time(self) -> None:
        for query in ("signals", "bars"):
            with self.subTest(query=query), self.assertRaises(ValidationError):
                validate_intent(
                    {
                        "intent_type": "read_only",
                        "query": query,
                        "symbol": "QQQ",
                    }
                )

    def test_pending_change_intent_covers_config_change_actions(self) -> None:
        for action in ["add_symbol", "remove_symbol", "enable_strategy", "disable_strategy", "change_watch_window"]:
            with self.subTest(action=action):
                intent = validate_intent(
                    {
                        "intent_type": "pending_change",
                        "action": action,
                        "symbol": "nvda",
                        "strategy_id": "macd",
                        "reason": "user requested change",
                    }
                )

                self.assertIsInstance(intent, PendingChangeIntent)
                self.assertEqual(intent.risk, "pending_change")
                self.assertEqual(intent.symbol, "NVDA")

    def test_high_risk_intent_is_never_executable(self) -> None:
        intent = validate_intent(
            {
                "intent_type": "high_risk_blocked",
                "requested_action": "place_order",
                "blocked_reason": "order placement is outside this agent boundary",
            }
        )

        self.assertIsInstance(intent, HighRiskBlockedIntent)
        self.assertFalse(intent.executable)
        self.assertIn("观察信号", intent.safety_message)

    def test_unknown_or_ambiguous_result_must_be_clarification_not_guess(self) -> None:
        intent = validate_intent(
            {
                "intent_type": "clarification",
                "question": "你想查询 signals、health 还是 trace？",
                "candidates": ["signals", "health", "trace"],
            }
        )

        self.assertIsInstance(intent, ClarificationIntent)
        self.assertFalse(intent.executable)

    def test_schema_rejects_unknown_query_and_extra_fields(self) -> None:
        with self.assertRaises(ValidationError):
            validate_intent({"intent_type": "read_only", "query": "portfolio"})

        with self.assertRaises(ValidationError):
            validate_intent({"intent_type": "read_only", "query": "signals", "api_key": "secret"})

    def test_json_schema_contains_risk_classes(self) -> None:
        schema_text = str(intent_json_schema())

        self.assertIn("read_only", schema_text)
        self.assertIn("pending_change", schema_text)
        self.assertIn("local_admin", schema_text)
        self.assertIn("high_risk_blocked", schema_text)


if __name__ == "__main__":
    unittest.main()
