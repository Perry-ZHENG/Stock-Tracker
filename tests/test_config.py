import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from stock_agent.config import DEFAULT_CONFIG, default_config_yaml, init_config, render_config_yaml, validate_config
from stock_agent.config_loader import load_config, reload_config


class ConfigTests(unittest.TestCase):
    '''
    Test cases for configuration validation and initialization.
    input: None
    output: Test cases that validate the default configuration and the init_config function.
    '''
    def test_default_config_validates(self) -> None:
        config = validate_config(DEFAULT_CONFIG)

        self.assertEqual(config.app.name, "stock-agent")
        self.assertEqual(config.provider.default, "twelve_data")
        self.assertEqual(config.input_control.request_ttl_sec, 600)
        self.assertEqual(config.llm.provider, "openrouter")
        self.assertEqual(
            config.llm.model,
            "qwen/qwen3-next-80b-a3b-instruct:free",
        )
        self.assertEqual(config.llm.fallback_model, "openrouter/free")
        self.assertEqual(config.llm.api_key_env, "OPENROUTER_API_KEY")
        self.assertEqual(config.llm.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(config.llm.request_timeout_sec, 45)
        self.assertEqual(config.llm.max_retries, 0)
        self.assertEqual(config.strategies.ma_cross.pairs[0], (3, 5))

    def test_default_config_yaml_contains_required_sections(self) -> None:
        yaml_text = default_config_yaml()

        for section in (
            "app:",
            "provider:",
            "symbols:",
            "bar:",
            "schedule:",
            "strategies:",
            "telegram:",
            "news:",
            "llm:",
            "storage:",
            "health:",
        ):
            self.assertIn(section, yaml_text)

    def test_init_config_creates_files_without_real_secrets(self) -> None:
        # Test that init_config creates the config.yaml and .env.example files with the expected content
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = init_config(Path(tmp_dir))

            self.assertTrue(result.config_written)
            self.assertTrue(result.env_example_written)
            self.assertTrue(result.config_path.exists())
            self.assertTrue(result.env_example_path.exists())
            self.assertIn("MARKET_DATA_API_KEY=", result.env_example_path.read_text())
            self.assertNotIn("sk-", result.env_example_path.read_text())

    def test_init_config_does_not_overwrite_without_force(self) -> None:
        # test that init_config does not overwrite existing config.yaml without force=True
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)
            config_path = root / "configs" / "config.yaml"
            config_path.write_text("custom: true\n", encoding="utf-8")

            result = init_config(root)

            self.assertFalse(result.config_written)
            self.assertEqual(config_path.read_text(encoding="utf-8"), "custom: true\n")

    def test_init_config_overwrites_with_force(self) -> None:
        # test that init_config overwrites existing config.yaml with force=True
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)
            config_path = root / "configs" / "config.yaml"
            config_path.write_text("custom: true\n", encoding="utf-8")

            result = init_config(root, force=True)

            self.assertTrue(result.config_written)
            self.assertIn("app:", config_path.read_text(encoding="utf-8"))

    def test_load_config_reads_yaml_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)

            context = load_config(root)

        self.assertFalse(context.used_defaults)
        self.assertEqual(context.config.provider.default, "twelve_data")
        self.assertEqual(context.config.symbols.default, ["AAPL", "MSFT", "NVDA"])
        self.assertEqual(context.config_path.name, "config.yaml")

    def test_load_config_uses_stock_agent_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            custom_config = deepcopy(DEFAULT_CONFIG)
            custom_config["storage"]["sqlite_path"] = "custom/runtime.sqlite"
            custom_config["symbols"]["default"] = ["QQQ"]
            config_path = root / "custom" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(render_config_yaml(custom_config), encoding="utf-8")

            with patch.dict("os.environ", {"STOCK_AGENT_CONFIG": "custom/config.yaml"}):
                context = load_config(root)

        self.assertEqual(context.config.storage.sqlite_path, "custom/runtime.sqlite")
        self.assertEqual(context.config.symbols.default, ["QQQ"])
        self.assertEqual(context.config_path, config_path)

    def test_reload_config_failure_does_not_mutate_current_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)
            current = load_config(root)
            current.config_path.write_text("app:\n  name: broken\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                reload_config(current, root=root)

        self.assertEqual(current.config.app.name, "stock-agent")
        self.assertEqual(current.config.provider.default, "twelve_data")


if __name__ == "__main__":
    unittest.main()
