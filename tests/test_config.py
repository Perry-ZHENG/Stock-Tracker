import tempfile
import unittest
from pathlib import Path

from stock_agent.config import DEFAULT_CONFIG, default_config_yaml, init_config, validate_config


class ConfigTests(unittest.TestCase):
    def test_default_config_validates(self) -> None:
        config = validate_config(DEFAULT_CONFIG)

        self.assertEqual(config.app.name, "stock-agent")
        self.assertEqual(config.provider.default, "csv_demo")
        self.assertEqual(config.strategies.ma_cross.pairs[0], (3, 5))

    def test_default_config_yaml_contains_required_sections(self) -> None:
        yaml_text = default_config_yaml()

        for section in (
            "app:",
            "provider:",
            "symbols:",
            "bar:",
            "strategies:",
            "telegram:",
            "news:",
            "llm:",
            "storage:",
            "health:",
        ):
            self.assertIn(section, yaml_text)

    def test_init_config_creates_files_without_real_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = init_config(Path(tmp_dir))

            self.assertTrue(result.config_written)
            self.assertTrue(result.env_example_written)
            self.assertTrue(result.config_path.exists())
            self.assertTrue(result.env_example_path.exists())
            self.assertIn("MARKET_DATA_API_KEY=", result.env_example_path.read_text())
            self.assertNotIn("sk-", result.env_example_path.read_text())

    def test_init_config_does_not_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)
            config_path = root / "configs" / "config.yaml"
            config_path.write_text("custom: true\n", encoding="utf-8")

            result = init_config(root)

            self.assertFalse(result.config_written)
            self.assertEqual(config_path.read_text(encoding="utf-8"), "custom: true\n")

    def test_init_config_overwrites_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)
            config_path = root / "configs" / "config.yaml"
            config_path.write_text("custom: true\n", encoding="utf-8")

            result = init_config(root, force=True)

            self.assertTrue(result.config_written)
            self.assertIn("app:", config_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
