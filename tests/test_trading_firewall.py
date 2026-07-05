import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.commands.interactive_cli import run_interactive_cli
from stock_agent.dialog.intents import HighRiskBlockedIntent
from stock_agent.dialog.llm_parser import LlmParser
from stock_agent.schemas import Signal
from stock_agent.security.trading_firewall import OBSERVATION_ONLY_MESSAGE, TradingActionFirewall
from stock_agent.storage.repositories import insert_signal, list_security_audit
from stock_agent.storage.sqlite import initialize_database, initialize_runtime_database, open_database
from stock_agent.telegram.bot import TelegramBot, TelegramBotSettings, TelegramUpdate


class TradingFirewallTests(unittest.TestCase):
    def test_firewall_audits_blocked_action_with_redaction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            intent = HighRiskBlockedIntent(
                source="llm",
                raw_text="place order for account_id=ACC-123 token=secret-token",
                requested_action="place_order",
                blocked_reason="test high-risk order",
            )

            decision = TradingActionFirewall(connection).inspect_intent(
                intent,
                actor_ref="account_id=ACC-123",
                details={"account_id": "ACC-123", "token": "secret-token"},
            )
            rows = list_security_audit(connection)
            connection.close()

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.message, OBSERVATION_ONLY_MESSAGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["action"], "place_order")
        self.assertEqual(rows[0]["decision"], "blocked")
        audit_text = str(rows[0])
        self.assertNotIn("ACC-123", audit_text)
        self.assertNotIn("secret-token", audit_text)

    def test_cli_order_request_is_blocked_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output = io.StringIO()

            exit_code = run_interactive_cli(
                root,
                input_stream=io.StringIO("buy 10 shares of QQQ for account_id=ACC-123\nexit\n"),
                output_stream=output,
            )
            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            rows = list_security_audit(connection)
            connection.close()

        self.assertEqual(exit_code, 0)
        self.assertIn("blocked=place_order", output.getvalue())
        self.assertIn("audit_id=", output.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "cli")
        self.assertNotIn("ACC-123", str(rows[0]))

    def test_telegram_order_request_is_blocked_and_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            bot = TelegramBot(root=root, connection=connection, settings=_settings())

            result = bot.handle_update(TelegramUpdate(user_id=1, chat_id=100, text="替我下单买入 QQQ account_id=ACC-123"))
            rows = list_security_audit(connection)
            connection.close()

        self.assertFalse(result.ok)
        self.assertIn("观察信号", result.text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "telegram")
        self.assertNotIn("ACC-123", str(rows[0]))

    def test_llm_order_intent_is_blocked_by_firewall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            intent = LlmParser(enabled=True, client=lambda _prompt: "{}").parse("place order to buy QQQ")

            decision = TradingActionFirewall(connection).inspect_intent(intent, source="llm", actor_ref="llm")
            rows = list_security_audit(connection)
            connection.close()

        self.assertFalse(decision.allowed)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "llm")

    def test_read_only_signal_query_bypasses_firewall_and_does_not_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(connection, _signal())
            connection.close()
            output = io.StringIO()

            exit_code = run_interactive_cli(
                root,
                input_stream=io.StringIO(
                    "查询 QQQ 从 2026-05-22 09:30 到 2026-05-22 16:00 "
                    "的信号，America/New_York\nyes\nexit\n"
                ),
                output_stream=output,
            )
            connection = open_database(root / "data/runtime/stock_agent.sqlite")
            rows = list_security_audit(connection)
            connection.close()

        self.assertEqual(exit_code, 0)
        self.assertIn("sig-001", output.getvalue())
        self.assertEqual(rows, [])


def _settings() -> TelegramBotSettings:
    return TelegramBotSettings(
        token="token",
        allowed_user_ids=[1],
        admin_user_ids=[],
        allowed_chat_ids=[],
    )


def _signal() -> Signal:
    return Signal(
        signal_id="sig-001",
        strategy_id="ma_cross",
        symbol="QQQ",
        timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
        direction="buy_watch",
        strength=0.7,
        confidence=0.8,
        reason="MA3 crossed above MA5",
        trace_id="trace-sig-001",
        source_bar_ids=["bar-001"],
        data_quality="normal",
        created_at=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
    )


if __name__ == "__main__":
    unittest.main()
