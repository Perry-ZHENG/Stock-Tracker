import copy
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.config_review import run_config_review
from stock_agent.config import DEFAULT_CONFIG, init_config
from stock_agent.config_changes import create_config_change
from stock_agent.storage.sqlite import initialize_runtime_database


class ConfigReviewCliTests(unittest.TestCase):
    def test_review_lists_pending_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            create_config_change(
                connection,
                change_id="chg-001",
                source="telegram",
                before_config=DEFAULT_CONFIG,
                after_config=_config_with_symbol("QQQ"),
                diff="symbols.default +QQQ",
            )
            connection.close()
            stream = io.StringIO()

            exit_code = run_config_review(root, action="review", stream=stream)

        self.assertEqual(exit_code, 0)
        self.assertIn("chg-001", stream.getvalue())
        self.assertIn("pending_review", stream.getvalue())

    def test_approve_from_cli_writes_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)
            connection = initialize_runtime_database(root)
            create_config_change(
                connection,
                change_id="chg-001",
                source="telegram",
                before_config=DEFAULT_CONFIG,
                after_config=_config_with_symbol("QQQ"),
                diff="symbols.default +QQQ",
            )
            connection.close()
            stream = io.StringIO()

            exit_code = run_config_review(root, action="approve", change_id="chg-001", stream=stream)
            config_text = (root / "configs" / "config.yaml").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn("status=applied", stream.getvalue())
        self.assertIn("QQQ", config_text)

    def test_stock_agent_cli_review_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_runtime_database(root)
            create_config_change(
                connection,
                change_id="chg-001",
                source="telegram",
                before_config=DEFAULT_CONFIG,
                after_config=_config_with_symbol("QQQ"),
                diff="symbols.default +QQQ",
            )
            connection.close()

            with patch("pathlib.Path.cwd", return_value=root):
                self.assertEqual(main(["cli", "review"]), 0)


def _config_with_symbol(symbol: str):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["symbols"]["default"] = [*config["symbols"]["default"], symbol]
    return config


if __name__ == "__main__":
    unittest.main()
