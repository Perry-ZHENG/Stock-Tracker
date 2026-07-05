import json
import unittest

from stock_agent.dialog.intents import ClarificationIntent, HighRiskBlockedIntent, PendingChangeIntent, ReadOnlyIntent
from stock_agent.dialog.llm_parser import LlmParser


class LlmParserTests(unittest.TestCase):
    def test_disabled_llm_falls_back_to_structured_parser_without_key(self) -> None:
        intent = LlmParser(enabled=False).parse("show signals QQQ limit 5")

        self.assertIsInstance(intent, ClarificationIntent)
        self.assertIn("IANA 时区", intent.question)

    def test_mock_llm_output_must_validate_against_intent_schema(self) -> None:
        parser = LlmParser(
            enabled=True,
            client=lambda _prompt: json.dumps(
                {
                    "intent_type": "pending_change",
                    "action": "add_symbol",
                    "symbol": "qqq",
                }
            ),
        )

        intent = parser.parse("please follow qqq")

        self.assertIsInstance(intent, PendingChangeIntent)
        self.assertEqual(intent.source, "llm")
        self.assertEqual(intent.symbol, "QQQ")

    def test_invalid_json_returns_clarification(self) -> None:
        intent = LlmParser(enabled=True, client=lambda _prompt: "not json").parse("recent signals")

        self.assertIsInstance(intent, ClarificationIntent)
        self.assertFalse(intent.executable)

    def test_unauthorized_local_admin_intent_is_not_executable(self) -> None:
        parser = LlmParser(
            enabled=True,
            client=lambda _prompt: json.dumps(
                {
                    "intent_type": "local_admin",
                    "action": "reload_config",
                    "dry_run": False,
                }
            ),
        )

        intent = parser.parse("reload the config now")

        self.assertIsInstance(intent, ClarificationIntent)
        self.assertFalse(intent.executable)

    def test_ambiguous_symbol_returns_clarification(self) -> None:
        parser = LlmParser(
            enabled=True,
            client=lambda _prompt: json.dumps(
                {
                    "intent_type": "read_only",
                    "query": "signals",
                    "symbol": "tech stocks",
                }
            ),
        )

        intent = parser.parse("recent signals for tech stocks")

        self.assertIsInstance(intent, ClarificationIntent)

    def test_high_risk_language_is_blocked_before_llm_output(self) -> None:
        called = False

        def client(_prompt: str) -> str:
            nonlocal called
            called = True
            return "{}"

        intent = LlmParser(enabled=True, client=client).parse("替我下单买入 QQQ，保证收益")

        self.assertIsInstance(intent, HighRiskBlockedIntent)
        self.assertFalse(intent.executable)
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
