import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.providers.base import MarketDataProvider
from stock_agent.providers.registry import ProviderRegistry, ProviderRegistryError
from stock_agent.schemas import Bar
from stock_agent.storage.repositories import list_health_metrics, list_notifications, list_trace_chain
from stock_agent.storage.sqlite import initialize_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ProviderRegistryTests(unittest.TestCase):
    def test_fallback_success_returns_standard_bars_and_audits_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            config = _config(
                default="live",
                priority=["live"],
                fallback_order=["csv_demo"],
            )
            registry = ProviderRegistry(
                root=PROJECT_ROOT,
                config=config,
                connection=connection,
                provider_factories={
                    "live": lambda: _FailingProvider("rate limit reached"),
                },
            )

            result = registry.fetch_intraday_bars(symbols=["QQQ"], interval="30m")
            traces = list_trace_chain(connection)
            metrics = list_health_metrics(connection)
            notifications = list_notifications(connection)

        self.assertEqual(result.provider_name, "csv_demo")
        self.assertTrue(result.fallback_used)
        self.assertEqual([attempt.provider_name for attempt in result.attempts], ["live", "csv_demo"])
        self.assertEqual(result.attempts[0].error_type, "rate_limit")
        self.assertGreater(len(result.bars), 0)
        self.assertIsInstance(result.bars[0], Bar)
        self.assertEqual(result.bars[0].source, "demo_csv")
        self.assertEqual(traces[0].module, "provider_registry")
        self.assertEqual(traces[0].status, "success")
        self.assertEqual(metrics[0].module, "provider_registry")
        self.assertEqual(metrics[0].status, "degraded")
        self.assertEqual(notifications[0]["channel"], "provider_registry")
        self.assertEqual(notifications[0]["status"], "pending")
        self.assertEqual(notifications[0]["payload"]["provider"], "csv_demo")

    def test_all_providers_failed_raises_and_records_failure_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            config = _config(
                default="live",
                priority=["live"],
                fallback_order=["cache"],
            )
            registry = ProviderRegistry(
                root=PROJECT_ROOT,
                config=config,
                connection=connection,
                provider_factories={
                    "live": lambda: _FailingProvider("timeout while fetching bars"),
                    "cache": lambda: _FailingProvider("cache missing bars"),
                },
            )

            with self.assertRaisesRegex(ProviderRegistryError, "all configured market data providers failed"):
                registry.fetch_intraday_bars(symbols=["QQQ"], interval="30m")

            traces = list_trace_chain(connection)
            metrics = list_health_metrics(connection)
            notifications = list_notifications(connection)

        self.assertEqual(traces[0].status, "failed")
        self.assertIn("timeout", traces[0].error_msg or "")
        self.assertEqual(metrics[0].status, "unhealthy")
        self.assertEqual(metrics[0].details["attempts"][0]["error_type"], "latency")
        self.assertEqual(notifications[0]["payload"]["provider"], None)
        self.assertEqual(notifications[0]["payload"]["message"], "all providers failed")

    def test_live_configuration_error_can_fallback_to_csv_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            config = _config(
                default="live",
                priority=["live"],
                fallback_order=["csv_demo"],
            )

            with patch.dict(os.environ, {}, clear=True):
                result = ProviderRegistry(
                    root=PROJECT_ROOT,
                    config=config,
                    connection=connection,
                ).fetch_intraday_bars(symbols=["QQQ"], interval="30m")

        self.assertEqual(result.provider_name, "csv_demo")
        self.assertEqual(result.attempts[0].error_type, "configuration")


class _FailingProvider(MarketDataProvider):
    def __init__(self, message: str) -> None:
        self.message = message

    def fetch_intraday_bars(self, *args, **kwargs):
        raise RuntimeError(self.message)


def _config(*, default: str, priority: list[str], fallback_order: list[str]):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["provider"]["default"] = default
    config["provider"]["priority"] = priority
    config["provider"]["fallback"] = {
        "enabled": True,
        "order": fallback_order,
    }
    config["provider"]["live"]["name"] = "alpha_vantage"
    config["provider"]["live"]["api_key_env"] = "MARKET_DATA_API_KEY_NOT_SET"
    return validate_config(config)


if __name__ == "__main__":
    unittest.main()
