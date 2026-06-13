import copy
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from stock_agent.broker import BrokerAdapter, BrokerCapabilities
from stock_agent.config import DEFAULT_CONFIG, validate_config
from stock_agent.providers.broker_market_data import BrokerMarketDataProvider, BrokerMarketDataProviderError
from stock_agent.providers.registry import ProviderRegistry
from stock_agent.schemas import Bar
from stock_agent.storage.sqlite import initialize_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BrokerMarketDataProviderTests(unittest.TestCase):
    def test_disabled_provider_fails_with_environment_context(self) -> None:
        provider = BrokerMarketDataProvider(adapter=_BrokerAdapter(), environment="sandbox", enabled=False)

        with self.assertRaisesRegex(BrokerMarketDataProviderError, "environment=sandbox"):
            provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")

    def test_enabled_sandbox_provider_returns_standard_bars(self) -> None:
        provider = BrokerMarketDataProvider(adapter=_BrokerAdapter(), environment="sandbox", enabled=True)

        bars = provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")
        health = provider.fetch_provider_health()

        self.assertEqual(len(bars), 1)
        self.assertIsInstance(bars[0], Bar)
        self.assertEqual(bars[0].source, "broker_sandbox")
        self.assertEqual(health["environment"], "sandbox")

    def test_live_environment_is_disabled_by_default(self) -> None:
        provider = BrokerMarketDataProvider(adapter=_BrokerAdapter(), environment="live", enabled=True)

        with self.assertRaisesRegex(BrokerMarketDataProviderError, "live broker market data is disabled"):
            provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")

    def test_registry_can_fallback_from_unconfigured_broker_to_csv_demo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_database(Path(tmp_dir) / "runtime.sqlite")
            config = _config(default="broker", priority=["broker"], fallback_order=["csv_demo"])

            result = ProviderRegistry(root=PROJECT_ROOT, config=config, connection=connection).fetch_intraday_bars(
                symbols=["QQQ"],
                interval="30m",
            )
            connection.close()

        self.assertEqual(result.provider_name, "csv_demo")
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.attempts[0].provider_name, "broker")
        self.assertEqual(result.attempts[0].error_type, "configuration")


class _BrokerAdapter(BrokerAdapter):
    capabilities = BrokerCapabilities(market_data=True)

    def fetch_market_data(self, *args, **kwargs) -> list[Bar]:
        return [
            Bar(
                bar_id="QQQ-30m-2026-05-22T15:30:00Z-broker_sandbox",
                symbol="QQQ",
                timestamp=datetime(2026, 5, 22, 15, 30, tzinfo=UTC),
                interval="30m",
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=1000,
                vwap=100.3,
                source="broker_sandbox",
            )
        ]


def _config(*, default: str, priority: list[str], fallback_order: list[str]):
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["provider"]["default"] = default
    config["provider"]["priority"] = priority
    config["provider"]["fallback"] = {
        "enabled": True,
        "order": fallback_order,
    }
    return validate_config(config)


if __name__ == "__main__":
    unittest.main()
