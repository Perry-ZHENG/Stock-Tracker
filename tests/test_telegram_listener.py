import io
import tempfile
import unittest
from pathlib import Path

from stock_agent.commands.telegram import run_telegram
from stock_agent.storage.repositories import list_config_changes
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.telegram import handle_telegram_message, resolve_telegram_role


class TelegramListenerTests(unittest.TestCase):
    def test_run_telegram_without_enabled_config_does_not_block_demo(self) -> None:
        stream = io.StringIO()

        exit_code = run_telegram(Path("/unused"), stream=stream)

        self.assertEqual(exit_code, 0)
        self.assertIn("telegram_status=disabled", stream.getvalue())

    def test_resolve_telegram_role_distinguishes_user_and_admin(self) -> None:
        self.assertEqual(
            resolve_telegram_role(user_id=1, allowed_user_ids=[1], admin_user_ids=[]),
            "user",
        )
        self.assertEqual(
            resolve_telegram_role(user_id=2, allowed_user_ids=[1], admin_user_ids=[2]),
            "admin",
        )
        self.assertIsNone(
            resolve_telegram_role(user_id=3, allowed_user_ids=[1], admin_user_ids=[2])
        )

    def test_rejects_unallowed_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))

            result = handle_telegram_message(
                root=Path(tmp_dir),
                connection=connection,
                user_id=99,
                text="/signals",
                allowed_user_ids=[1],
                admin_user_ids=[2],
            )

        self.assertFalse(result.ok)
        self.assertIn("not allowed", result.message)

    def test_supports_signal_health_news_queries_for_allowed_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)

            signals_result = handle_telegram_message(
                root=root,
                connection=connection,
                user_id=1,
                text="/signals",
                allowed_user_ids=[1],
                admin_user_ids=[],
            )
            health_result = handle_telegram_message(
                root=root,
                connection=connection,
                user_id=1,
                text="/health",
                allowed_user_ids=[1],
                admin_user_ids=[],
            )
            news_result = handle_telegram_message(
                root=root,
                connection=connection,
                user_id=1,
                text="/news",
                allowed_user_ids=[1],
                admin_user_ids=[],
            )

        self.assertTrue(signals_result.ok)
        self.assertIn("signals:", signals_result.message)
        self.assertTrue(health_result.ok)
        self.assertIn("health:", health_result.message)
        self.assertTrue(news_result.ok)
        self.assertIn("news:", news_result.message)

    def test_user_cannot_create_config_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)

            result = handle_telegram_message(
                root=root,
                connection=connection,
                user_id=1,
                text="/config add-symbol QQQ",
                allowed_user_ids=[1],
                admin_user_ids=[],
            )

        self.assertFalse(result.ok)
        self.assertIn("admin role", result.message)

    def test_admin_config_change_enters_pending_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)

            result = handle_telegram_message(
                root=root,
                connection=connection,
                user_id=2,
                text="/config add-symbol QQQ",
                allowed_user_ids=[1],
                admin_user_ids=[2],
            )
            changes = list_config_changes(connection)

        self.assertTrue(result.ok)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["status"], "pending_review")
        self.assertEqual(changes[0]["source"], "telegram")
        self.assertIn("requires CLI review", result.message)


if __name__ == "__main__":
    unittest.main()
