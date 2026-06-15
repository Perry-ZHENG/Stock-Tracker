import io
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.retention import run_retention
from stock_agent.storage.retention import build_retention_plan, execute_retention_plan, format_retention_plan


class RetentionTests(unittest.TestCase):
    def test_build_retention_plan_marks_temp_and_news_actions(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "data" / "lake"
            raw_path = self._write(root / "raw_bars/date=2026-06-01/part-00000.jsonl")
            feature_path = self._write(root / "features/date=2026-06-01/part-00000.jsonl")
            signal_path = self._write(root / "signals/date=2026-06-01/part-00000.jsonl")
            news_path = self._write(root / "news/date=2026-06-01/news.jsonl")

            plan = build_retention_plan(root, today=date(2026, 6, 15))

            actions = {item.path: item.action for item in plan.items}
            self.assertEqual(actions[raw_path], "delete_temp")
            self.assertEqual(actions[feature_path], "delete_temp")
            self.assertEqual(actions[signal_path], "keep")
            self.assertEqual(actions[news_path], "compress_news")
            self.assertEqual(plan.affected_count, 3)

    def test_dry_run_does_not_modify_files(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "data" / "lake"
            raw_path = self._write(root / "raw_bars/date=2026-06-01/part-00000.jsonl")
            plan = build_retention_plan(root, today=date(2026, 6, 15))

            result = execute_retention_plan(plan, execute=False)

            self.assertTrue(raw_path.exists())
            self.assertTrue(result.dry_run)
            self.assertFalse(result.executed)

    def test_format_retention_plan_includes_audit_details(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir) / "data" / "lake"
            self._write(root / "news/date=2026-06-01/news.jsonl")
            plan = build_retention_plan(root, today=date(2026, 6, 15))

            output = format_retention_plan(plan)

            self.assertIn("dry_run=true", output)
            self.assertIn("affected_count=1", output)
            self.assertIn("compress_news | news", output)

    def test_run_retention_defaults_to_dry_run(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            raw_path = self._write(root / "data/lake/raw_bars/date=2026-06-01/part-00000.jsonl")
            stream = io.StringIO()

            result = run_retention(root, stream=stream)

            self.assertTrue(result.ok)
            self.assertTrue(result.plan.dry_run)
            self.assertTrue(raw_path.exists())
            self.assertIn("dry_run=true", stream.getvalue())

    def test_cli_retention_command_runs_dry_run(self) -> None:
        with TemporaryDirectory() as tmp_dir, patch("pathlib.Path.cwd", return_value=Path(tmp_dir)):
            self._write(Path(tmp_dir) / "data/lake/raw_bars/date=2026-06-01/part-00000.jsonl")

            self.assertEqual(main(["retention"]), 0)

    def _write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"symbol":"AAPL"}\n', encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
