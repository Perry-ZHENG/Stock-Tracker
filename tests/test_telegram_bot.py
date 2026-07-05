import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.schemas import Signal
from stock_agent.storage.repositories import insert_signal, list_config_changes
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.telegram.bot import TelegramBot, TelegramBotSettings, TelegramUpdate, check_telegram_bot_startup


class TelegramBotTests(unittest.TestCase):
    def test_missing_token_returns_clear_startup_status(self) -> None:
        startup = check_telegram_bot_startup(_settings(token=None))

        self.assertFalse(startup.ok)
        self.assertEqual(startup.status, "disabled")
        self.assertIn("missing telegram token", startup.reason or "")

    def test_allowlists_user_and_chat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            bot = TelegramBot(root=Path(tmp_dir), connection=connection, settings=_settings(allowed_chat_ids=[100]))

            denied_user = bot.handle_update(TelegramUpdate(user_id=99, chat_id=100, text="/signals"))
            denied_chat = bot.handle_update(TelegramUpdate(user_id=1, chat_id=200, text="/signals"))
            connection.close()

        self.assertFalse(denied_user.ok)
        self.assertIn("user is not allowed", denied_user.text)
        self.assertFalse(denied_chat.ok)
        self.assertIn("chat is not allowed", denied_chat.text)

    def test_slash_and_natural_language_queries_use_query_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            insert_signal(connection, _signal())
            bot = TelegramBot(root=root, connection=connection, settings=_settings())

            slash = bot.handle_update(TelegramUpdate(user_id=1, chat_id=100, text="/signals"))
            natural = bot.handle_update(
                TelegramUpdate(
                    user_id=1,
                    chat_id=100,
                    text=(
                        "查询 QQQ 从 2026-05-22 09:30 到 2026-05-22 16:00 "
                        "的信号，America/New_York"
                    ),
                )
            )
            connection.close()

        self.assertTrue(slash.ok)
        self.assertTrue(natural.ok)
        self.assertIn("sig-001", slash.text)
        self.assertIn("sig-001", natural.text)

    def test_natural_language_config_change_requires_admin_and_pending_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            bot = TelegramBot(root=root, connection=connection, settings=_settings(admin_user_ids=[2]))

            user_result = bot.handle_update(TelegramUpdate(user_id=1, chat_id=100, text="添加 QQQ 到关注"))
            admin_result = bot.handle_update(TelegramUpdate(user_id=2, chat_id=100, text="添加 QQQ 到关注"))
            changes = list_config_changes(connection)
            connection.close()

        self.assertFalse(user_result.ok)
        self.assertIn("admin role", user_result.text)
        self.assertTrue(admin_result.ok)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["status"], "pending_review")
        self.assertEqual(changes[0]["source"], "telegram")

    def test_high_risk_natural_language_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            bot = TelegramBot(root=Path(tmp_dir), connection=connection, settings=_settings())

            result = bot.handle_update(TelegramUpdate(user_id=1, chat_id=100, text="替我下单买入 QQQ"))
            connection.close()

        self.assertFalse(result.ok)
        self.assertIn("观察信号", result.text)


def _settings(
    *,
    token: str | None = "token",
    allowed_chat_ids: list[int] | None = None,
    admin_user_ids: list[int] | None = None,
) -> TelegramBotSettings:
    return TelegramBotSettings(
        token=token,
        allowed_user_ids=[1],
        admin_user_ids=admin_user_ids or [],
        allowed_chat_ids=allowed_chat_ids or [],
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
