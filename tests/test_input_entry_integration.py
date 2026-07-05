import io
import tempfile
import unittest
from pathlib import Path

from stock_agent.commands.interactive_cli import run_interactive_cli
from stock_agent.dialog.input_gate import InputGate
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.telegram.bot import (
    TelegramBot,
    TelegramBotSettings,
    TelegramUpdate,
)


class InputEntryIntegrationTests(unittest.TestCase):
    def test_inactive_cli_can_request_switch_but_cannot_execute_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            gate = InputGate(connection)
            gate.check("telegram", actor_ref="telegram-bot")
            output = io.StringIO()

            exit_code = run_interactive_cli(
                root,
                input_stream=io.StringIO("health\nyes\nexit\n"),
                output_stream=output,
            )
            pending = gate.pending_for("telegram")
            connection.close()

        self.assertEqual(exit_code, 0)
        self.assertIn("input_status=blocked", output.getvalue())
        self.assertIn("input_switch_status=pending", output.getvalue())
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].to_source, "cli")

    def test_active_cli_can_approve_pending_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            gate = InputGate(connection)
            gate.check("cli", actor_ref="cli-session")
            request = gate.request_switch("fastapi", actor_ref="web-session")
            output = io.StringIO()

            run_interactive_cli(
                root,
                input_stream=io.StringIO(f"approve {request.request_id}\nexit\n"),
                output_stream=output,
            )
            state = gate.state()
            connection.close()

        self.assertIn("input_switch_status=approved", output.getvalue())
        self.assertEqual(state.active_source, "fastapi")

    def test_telegram_request_waits_for_original_interface_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            gate = InputGate(connection)
            gate.check("cli", actor_ref="cli-session")
            bot = TelegramBot(
                root=root,
                connection=connection,
                settings=TelegramBotSettings(
                    token="token",
                    allowed_user_ids=[1],
                    admin_user_ids=[],
                    allowed_chat_ids=[],
                ),
            )

            blocked = bot.handle_update(
                TelegramUpdate(user_id=1, chat_id=100, text="/signals")
            )
            requested = bot.handle_update(
                TelegramUpdate(user_id=1, chat_id=100, text="/input request")
            )
            request = gate.state().pending_requests[0]
            gate.decide(
                request.request_id,
                source="cli",
                actor_ref="cli-session",
                approve=True,
            )
            allowed = bot.handle_update(
                TelegramUpdate(user_id=1, chat_id=100, text="/signals")
            )
            connection.close()

        self.assertFalse(blocked.ok)
        self.assertIn("input_status=blocked", blocked.text)
        self.assertTrue(requested.ok)
        self.assertIn("input_switch_status=pending", requested.text)
        self.assertTrue(allowed.ok)


if __name__ == "__main__":
    unittest.main()
