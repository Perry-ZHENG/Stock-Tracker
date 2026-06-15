import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.deploy_validate import run_deploy_validate
from stock_agent.config import init_config
from stock_agent.deployment.validation import format_deploy_validation, validate_deployment


class DeployValidateTests(unittest.TestCase):
    def test_validate_deployment_passes_for_initialized_demo_project(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)
            self._write_demo_csv(root)

            result = validate_deployment(root)

            self.assertTrue(result.ok)
            self.assertTrue(any(check.name == "csv_demo" for check in result.checks))

    def test_validate_deployment_fails_when_config_is_missing(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            self._write_demo_csv(root)

            result = validate_deployment(root)

            self.assertFalse(result.ok)
            self.assertIn("config file missing", format_deploy_validation(result))

    def test_run_deploy_validate_outputs_dry_run_status(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            init_config(root)
            self._write_demo_csv(root)
            stream = io.StringIO()

            result = run_deploy_validate(root, stream=stream)

            self.assertTrue(result.ok)
            self.assertIn("deploy_validation_status=ok", stream.getvalue())
            self.assertIn("dry_run=true", stream.getvalue())

    def test_cli_deploy_validate_command(self) -> None:
        with TemporaryDirectory() as tmp_dir, patch("pathlib.Path.cwd", return_value=Path(tmp_dir)):
            root = Path(tmp_dir)
            init_config(root)
            self._write_demo_csv(root)

            self.assertEqual(main(["deploy-validate"]), 0)

    def _write_demo_csv(self, root: Path) -> None:
        sample = root / "data" / "sample" / "sample_bars.csv"
        sample.parent.mkdir(parents=True, exist_ok=True)
        sample.write_text(
            "timestamp,symbol,open,high,low,close,volume\n"
            "2026-05-22T13:30:00Z,AAPL,100,101,99,100.5,1000\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
