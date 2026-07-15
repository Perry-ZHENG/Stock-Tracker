import copy
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from stock_agent.bars import generate_bar_id
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
            connection.close()

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
            connection.close()

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
            connection.close()

        self.assertEqual(result.provider_name, "csv_demo")
        self.assertEqual(result.attempts[0].error_type, "configuration")

    def test_twelve_data_budget_is_shared_by_registry_instances_using_the_same_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            config = _config(
                default="twelve_data",
                priority=["twelve_data"],
                fallback_order=["csv_demo"],
                twelve_credit_budget=1,
            )
            primary_fetches = 0

            def primary_factory() -> _SuccessfulProvider:
                class _CountingPrimaryProvider(_SuccessfulProvider):
                    def fetch_intraday_bars(self, *args, **kwargs):
                        nonlocal primary_fetches
                        primary_fetches += 1
                        return super().fetch_intraday_bars(*args, **kwargs)

                return _CountingPrimaryProvider()

            first = ProviderRegistry(
                root=PROJECT_ROOT,
                config=config,
                connection=connection,
                provider_factories={"twelve_data": primary_factory, "csv_demo": _SuccessfulProvider},
            ).fetch_intraday_bars(symbols=["QQQ"], interval="30m")
            second = ProviderRegistry(
                root=PROJECT_ROOT,
                config=config,
                connection=connection,
                provider_factories={"twelve_data": primary_factory, "csv_demo": _SuccessfulProvider},
            ).fetch_intraday_bars(symbols=["QQQ"], interval="30m")
            reservations = connection.execute(
                "SELECT COALESCE(SUM(credits), 0) FROM provider_credit_reservations WHERE provider_name = 'twelve_data'"
            ).fetchone()[0]
            connection.close()

        self.assertEqual(first.provider_name, "twelve_data")
        self.assertEqual(second.provider_name, "csv_demo")
        self.assertEqual(second.attempts[0].error_type, "rate_limit")
        self.assertEqual(primary_fetches, 1)
        self.assertEqual(reservations, 1)

    def test_direct_twelve_credit_exhaustion_is_classified_as_rate_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            config = _config(
                default="twelve_data",
                priority=["twelve_data"],
                fallback_order=["csv_demo"],
            )
            result = ProviderRegistry(
                root=PROJECT_ROOT,
                config=config,
                connection=connection,
                provider_factories={
                    "twelve_data": lambda: _FailingProvider("Twelve Data minute credit budget exhausted"),
                    "csv_demo": _SuccessfulProvider,
                },
            ).fetch_intraday_bars(symbols=["QQQ"], interval="30m")
            connection.close()

        self.assertEqual(result.provider_name, "csv_demo")
        self.assertEqual(result.attempts[0].error_type, "rate_limit")


class _FailingProvider(MarketDataProvider):
    def __init__(self, message: str) -> None:
        self.message = message

    def fetch_intraday_bars(self, *args, **kwargs):
        raise RuntimeError(self.message)


class _SuccessfulProvider(MarketDataProvider):
    def fetch_intraday_bars(self, *args, **kwargs):
        timestamp = datetime(2026, 5, 22, 13, 30, tzinfo=UTC)
        return [
            Bar(
                bar_id=generate_bar_id("QQQ", "30m", timestamp.isoformat().replace("+00:00", "Z"), "fixture"),
                symbol="QQQ",
                timestamp=timestamp,
                interval="30m",
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1_000,
                vwap=100,
                source="fixture",
            )
        ]

    def fetch_provider_health(self) -> dict[str, str]:
        return {"status": "healthy"}


def _config(
    *,
    default: str,
    priority: list[str],
    fallback_order: list[str],
    twelve_credit_budget: int | None = None,
):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["provider"]["default"] = default
    config["provider"]["priority"] = priority
    config["provider"]["fallback"] = {
        "enabled": True,
        "order": fallback_order,
    }
    config["provider"]["live"]["name"] = "alpha_vantage"
    config["provider"]["live"]["api_key_env"] = "MARKET_DATA_API_KEY_NOT_SET"
    if twelve_credit_budget is not None:
        config["provider"]["twelve_data"]["credit_budget_per_minute"] = twelve_credit_budget
    return validate_config(config)


if __name__ == "__main__":
    unittest.main()
