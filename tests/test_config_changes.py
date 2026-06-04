import copy
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.config import DEFAULT_CONFIG
from stock_agent.config_changes import (
    ConfigChangeError,
    approve_config_change,
    create_config_change,
    reject_config_change,
)
from stock_agent.storage.repositories import get_config_change
from stock_agent.storage.sqlite import initialize_database


class ConfigChangeTests(unittest.TestCase):
    def test_telegram_change_enters_pending_review_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "stock_agent.sqlite")

            change = create_config_change(
                connection,
                change_id="chg-001",
                source="telegram",
                before_config=DEFAULT_CONFIG,
                after_config=_config_with_symbol("QQQ"),
                diff="symbols.default +QQQ",
                now=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
            )

            self.assertEqual(change["status"], "pending_review")
            with self.assertRaises(ConfigChangeError):
                create_config_change(
                    connection,
                    change_id="chg-002",
                    source="telegram",
                    before_config=DEFAULT_CONFIG,
                    after_config=_config_with_symbol("SPY"),
                    diff="symbols.default +SPY",
                    status="applied",
                )

    def test_cli_approve_writes_yaml_and_marks_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "configs" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("old: true\n", encoding="utf-8")
            connection = initialize_database(root / "stock_agent.sqlite")
            create_config_change(
                connection,
                change_id="chg-001",
                source="telegram",
                before_config=DEFAULT_CONFIG,
                after_config=_config_with_symbol("QQQ"),
                diff="symbols.default +QQQ",
            )

            change = approve_config_change(connection, change_id="chg-001", config_path=config_path)

            self.assertEqual(change["status"], "applied")
            self.assertIn("QQQ", config_path.read_text(encoding="utf-8"))

    def test_cli_reject_marks_rejected_without_writing_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "configs" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("old: true\n", encoding="utf-8")
            connection = initialize_database(root / "stock_agent.sqlite")
            create_config_change(
                connection,
                change_id="chg-001",
                source="telegram",
                before_config=DEFAULT_CONFIG,
                after_config=_config_with_symbol("QQQ"),
                diff="symbols.default +QQQ",
            )

            change = reject_config_change(connection, change_id="chg-001", reason="not now")

            self.assertEqual(change["status"], "rejected")
            self.assertEqual(config_path.read_text(encoding="utf-8"), "old: true\n")

    def test_reload_failure_restores_old_config_and_marks_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "configs" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("old: true\n", encoding="utf-8")
            connection = initialize_database(root / "stock_agent.sqlite")
            create_config_change(
                connection,
                change_id="chg-001",
                source="telegram",
                before_config=DEFAULT_CONFIG,
                after_config=_config_with_symbol("QQQ"),
                diff="symbols.default +QQQ",
            )

            with self.assertRaises(ConfigChangeError):
                approve_config_change(
                    connection,
                    change_id="chg-001",
                    config_path=config_path,
                    reload_validator=lambda _config: (_ for _ in ()).throw(ValueError("reload failed")),
                )

            self.assertEqual(config_path.read_text(encoding="utf-8"), "old: true\n")
            self.assertEqual(get_config_change(connection, "chg-001")["status"], "rollback")


def _config_with_symbol(symbol: str):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["symbols"]["default"] = [*config["symbols"]["default"], symbol]
    return config


if __name__ == "__main__":
    unittest.main()
