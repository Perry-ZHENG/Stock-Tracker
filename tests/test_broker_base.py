import io
import tempfile
import unittest
from pathlib import Path

from stock_agent.broker import BrokerActionBlocked, BrokerAdapter, BrokerCapabilities
from stock_agent.commands.interactive_cli import run_interactive_cli
from stock_agent.dialog.intents import HighRiskBlockedIntent
from stock_agent.dialog.llm_parser import LlmParser
from stock_agent.telegram.bot import TelegramBot, TelegramBotSettings, TelegramUpdate
from stock_agent.storage.sqlite import initialize_runtime_database


class BrokerBaseTests(unittest.TestCase):
    def test_default_broker_capabilities_are_explicitly_closed(self) -> None:
        capabilities = BrokerCapabilities()

        self.assertFalse(capabilities.market_data)
        self.assertFalse(capabilities.account_snapshot)
        self.assertFalse(capabilities.positions_snapshot)
        self.assertFalse(capabilities.broker_health)
        self.assertFalse(capabilities.order_placement)
        self.assertFalse(capabilities.order_modification)
        self.assertFalse(capabilities.withdrawal)
        self.assertFalse(capabilities.account_mutation)

    def test_order_and_account_mutation_methods_raise_blocked(self) -> None:
        adapter = BrokerAdapter()

        for method_name in ["place_order", "modify_order", "cancel_order", "withdraw_funds", "mutate_account"]:
            with self.subTest(method_name=method_name):
                with self.assertRaises(BrokerActionBlocked):
                    getattr(adapter, method_name)()

    def test_cli_llm_and_telegram_order_requests_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            output = io.StringIO()
            cli_exit = run_interactive_cli(
                root,
                input_stream=io.StringIO("buy 10 shares of QQQ\nexit\n"),
                output_stream=output,
            )
            llm_intent = LlmParser(enabled=True, client=lambda _prompt: "{}").parse("替我下单买入 QQQ")
            connection = initialize_runtime_database(root)
            bot = TelegramBot(
                root=root,
                connection=connection,
                settings=TelegramBotSettings(token="token", allowed_user_ids=[1], admin_user_ids=[], allowed_chat_ids=[]),
            )
            telegram_result = bot.handle_update(TelegramUpdate(user_id=1, chat_id=100, text="替我下单买入 QQQ"))
            connection.close()

        self.assertEqual(cli_exit, 0)
        self.assertIn("blocked=place_order", output.getvalue())
        self.assertIsInstance(llm_intent, HighRiskBlockedIntent)
        self.assertFalse(telegram_result.ok)
        self.assertIn("观察信号", telegram_result.text)


if __name__ == "__main__":
    unittest.main()
