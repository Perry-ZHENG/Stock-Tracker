import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from stock_agent.commands.telegram import run_telegram
from stock_agent.config import DEFAULT_CONFIG, render_config_yaml
from stock_agent.config_loader import load_config
from stock_agent.dialog.input_gate import InputGate
from stock_agent.storage.sqlite import initialize_runtime_database


class FakeTelegramApi:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    def get_updates(self, *, offset: int, timeout_sec: int) -> list[dict]:
        return []

    def send_message(self, *, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class TelegramInputTransportTests(unittest.TestCase):
    def test_active_telegram_receives_proactive_switch_approval_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = deepcopy(DEFAULT_CONFIG)
            config["telegram"]["enabled"] = True
            config["telegram"]["allowed_user_ids"] = [1]
            config_path = root / "configs" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(render_config_yaml(config), encoding="utf-8")
            context = load_config(root)

            connection = initialize_runtime_database(root, context.config)
            gate = InputGate.from_config(connection, context.config.input_control)
            gate.check("telegram", actor_ref="user:1:chat:100")
            request = gate.request_switch("fastapi", actor_ref="web:test")
            connection.close()
            api = FakeTelegramApi()

            with patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "test-token"}):
                exit_code = run_telegram(
                    root,
                    config_context=context,
                    api=api,  # type: ignore[arg-type]
                    once=True,
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(api.sent), 1)
        self.assertEqual(api.sent[0][0], 100)
        self.assertIn(request.request_id, api.sent[0][1])
        self.assertIn("/input approve", api.sent[0][1])


if __name__ == "__main__":
    unittest.main()
