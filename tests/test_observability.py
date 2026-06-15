import io
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from stock_agent.cli import main
from stock_agent.commands.health import run_health
from stock_agent.config_changes import create_config_change
from stock_agent.health import record_health_metric
from stock_agent.schemas import TraceChain
from stock_agent.storage.repositories import insert_abnormal_bar, insert_trace_chain
from stock_agent.storage.sqlite import initialize_database


class ObservabilityTests(unittest.TestCase):
    def test_health_verbose_outputs_module_observability_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            sqlite_path = root / "data/runtime/stock_agent.sqlite"
            connection = initialize_database(sqlite_path)
            now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
            record_health_metric(
                connection,
                module="provider_registry",
                now=now,
                details={"fallback_used": True, "api_key": "secret-key"},
            )
            record_health_metric(
                connection,
                module="supervisor",
                now=now + timedelta(minutes=1),
                error_rate=1,
                consecutive_failures=1,
                alert_failures=1,
                details={"token": "secret-token"},
            )
            insert_trace_chain(
                connection,
                TraceChain(
                    trace_id="trace-provider-fallback",
                    module="provider_registry",
                    input_ref={"provider": "primary"},
                    output_ref={"fallback_used": True},
                    status="failed",
                    error_msg="provider fallback",
                    created_at=now,
                ),
            )
            insert_abnormal_bar(
                connection,
                quarantine_id="quarantine-001",
                bar_id="bar-001",
                symbol="QQQ",
                timestamp=now,
                window="QQQ:30m:2026-06-15T12:00:00Z",
                reason="price jump",
                severity="unhealthy",
                status="quarantined",
                bar_payload={"api_key": "secret-key"},
                created_at=now,
                updated_at=now,
            )
            create_config_change(
                connection,
                change_id="chg-001",
                source="telegram",
                before_config={},
                after_config={},
                diff="test",
                status="pending_review",
                now=now,
            )
            connection.close()
            stream = io.StringIO()

            result = run_health(root, stream=stream, verbose=True)

        output = stream.getvalue()
        self.assertEqual(result.status, "unhealthy")
        self.assertIn("verbose_health_status=ok", output)
        self.assertIn("provider_registry | healthy", output)
        self.assertIn("supervisor | unhealthy", output)
        self.assertIn("abnormal_bar_count=1", output)
        self.assertIn("config_review_backlog=1", output)
        self.assertIn("supervisor_intercept_count=1", output)
        self.assertNotIn("secret-key", output)
        self.assertNotIn("secret-token", output)

    def test_cli_health_verbose_flag_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            connection = initialize_database(root / "data/runtime/stock_agent.sqlite")
            record_health_metric(connection, module="worker")
            connection.close()
            with patch("pathlib.Path.cwd", return_value=root), patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main(["health", "--verbose"])

        self.assertEqual(exit_code, 0)
        self.assertIn("verbose_health_status=ok", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
