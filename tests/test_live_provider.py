import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from stock_agent.providers.live import AlphaVantageProvider, LiveProviderError, create_live_provider


class LiveProviderTests(unittest.TestCase):
    def test_alpha_vantage_maps_provider_payload_to_standard_bars(self) -> None:
        provider = AlphaVantageProvider(api_key="demo", http_get=_fake_alpha_vantage_get)

        bars = provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].symbol, "QQQ")
        self.assertEqual(bars[0].interval, "30m")
        self.assertEqual(bars[0].source, "alpha_vantage")
        self.assertEqual(bars[0].timestamp, datetime(2026, 5, 22, 19, 30, tzinfo=UTC))
        self.assertEqual(bars[0].open, 470.0)
        self.assertEqual(bars[0].volume, 1000)
        self.assertTrue(bars[0].bar_id.startswith("QQQ-30m-2026-05-22T19:30:00Z-alpha_vantage"))

    def test_alpha_vantage_filters_time_range(self) -> None:
        provider = AlphaVantageProvider(api_key="demo", http_get=_fake_alpha_vantage_get)

        bars = provider.fetch_intraday_bars(
            symbols=["QQQ"],
            interval="30m",
            start=datetime(2026, 5, 22, 20, 0, tzinfo=UTC),
        )

        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].timestamp, datetime(2026, 5, 22, 20, 0, tzinfo=UTC))

    def test_provider_throttling_message_is_not_silently_accepted(self) -> None:
        provider = AlphaVantageProvider(
            api_key="demo",
            http_get=lambda _url, _params: {"Note": "rate limit reached"},
        )

        with self.assertRaises(LiveProviderError):
            provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")

    def test_missing_api_key_env_fails_readably(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(LiveProviderError, "missing API key env MARKET_DATA_API_KEY"):
                create_live_provider(provider_name="alpha_vantage", api_key_env="MARKET_DATA_API_KEY")

    def test_strategy_never_receives_provider_payload_shape(self) -> None:
        provider = AlphaVantageProvider(api_key="demo", http_get=_fake_alpha_vantage_get)

        bar = provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")[0]

        self.assertFalse(hasattr(bar, "Time Series (30min)"))
        self.assertFalse(hasattr(bar, "1. open"))

    def test_unsupported_live_provider_fails_readably(self) -> None:
        with self.assertRaisesRegex(LiveProviderError, "unsupported live provider"):
            create_live_provider(provider_name="polygon", api_key_env="POLYGON_API_KEY")


def _fake_alpha_vantage_get(_url, params):
    assert params["function"] == "TIME_SERIES_INTRADAY"
    assert params["symbol"] == "QQQ"
    assert params["interval"] == "30min"
    assert params["adjusted"] == "true"
    assert params["extended_hours"] == "false"
    return {
        "Meta Data": {
            "1. Information": "Intraday (30min) open, high, low, close prices and volume",
            "2. Symbol": "QQQ",
        },
        "Time Series (30min)": {
            "2026-05-22 16:00:00": {
                "1. open": "472.0000",
                "2. high": "473.0000",
                "3. low": "471.5000",
                "4. close": "472.5000",
                "5. volume": "1500",
            },
            "2026-05-22 15:30:00": {
                "1. open": "470.0000",
                "2. high": "472.0000",
                "3. low": "469.5000",
                "4. close": "471.0000",
                "5. volume": "1000",
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
