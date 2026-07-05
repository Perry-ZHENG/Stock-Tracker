import copy
import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.worker import run_worker
from stock_agent.config import DEFAULT_CONFIG, init_config, render_config_yaml
from stock_agent.config_loader import load_config, reload_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigLoaderTests(unittest.TestCase):
    def test_load_config_reads_default_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)

            context = load_config(root)

        self.assertFalse(context.used_defaults)
        self.assertEqual(context.config_path, root / "configs" / "config.yaml")
        self.assertEqual(context.config.provider.default, "twelve_data")
        self.assertEqual(len(context.version), 16)

    def test_stock_agent_config_env_overrides_default_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            custom_config = _custom_config(sqlite_path="custom/runtime.sqlite", symbols=["QQQ"])
            config_path = root / "custom" / "config.yaml"
            _write_config(config_path, custom_config)

            with patch.dict("os.environ", {"STOCK_AGENT_CONFIG": str(config_path)}):
                context = load_config(root)

        self.assertEqual(context.config_path, config_path)
        self.assertEqual(context.config.storage.sqlite_path, "custom/runtime.sqlite")
        self.assertEqual(context.config.symbols.default, ["QQQ"])

    def test_worker_once_uses_custom_storage_path_from_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_sample_data(root)
            config_path = root / "custom" / "worker.yaml"
            _write_config(config_path, _custom_config(sqlite_path="custom/runtime.sqlite"))
            stream = io.StringIO()

            exit_code = run_worker(
                root,
                once=True,
                interval_sec=0.01,
                stream=stream,
                config_context=load_config(root, config_path),
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "custom" / "runtime.sqlite").exists())
            self.assertFalse((root / "data" / "runtime" / "stock_agent.sqlite").exists())
            self.assertIn("worker_status=completed", stream.getvalue())

    def test_stock_agent_worker_once_respects_stock_agent_config_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            _copy_sample_data(root)
            config_path = root / "custom" / "worker.yaml"
            _write_config(config_path, _custom_config(sqlite_path="custom/runtime.sqlite"))

            with patch("pathlib.Path.cwd", return_value=root), patch.dict(
                "os.environ",
                {"STOCK_AGENT_CONFIG": str(config_path)},
            ):
                exit_code = main(["worker", "--once", "--interval-sec", "0.01"])

            self.assertEqual(exit_code, 0)
            self.assertTrue((root / "custom" / "runtime.sqlite").exists())

    def test_reload_config_failure_keeps_current_context_usable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "configs" / "config.yaml"
            _write_config(config_path, _custom_config(sqlite_path="data/runtime/ok.sqlite"))
            current = load_config(root, config_path)
            config_path.write_text("app:\n  name: broken\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                reload_config(current, root=root)

        self.assertEqual(current.config.storage.sqlite_path, "data/runtime/ok.sqlite")
        self.assertEqual(current.config.app.name, "stock-agent")


def _custom_config(*, sqlite_path: str, symbols: list[str] | None = None) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["storage"]["sqlite_path"] = sqlite_path
    if symbols is not None:
        config["symbols"]["default"] = symbols
    return config


def _write_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_config_yaml(config), encoding="utf-8")


def _copy_sample_data(root: Path) -> None:
    shutil.copytree(PROJECT_ROOT / "data" / "sample", root / "data" / "sample")


if __name__ == "__main__":
    unittest.main()
