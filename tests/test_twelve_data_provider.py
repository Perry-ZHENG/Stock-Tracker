from __future__ import annotations

import ssl
from unittest.mock import patch

import pytest

from stock_agent.providers.twelve_data import TwelveDataProvider, TwelveDataProviderError, _urllib_get_json


class _Response:
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"values": []}'


def test_twelve_http_client_uses_a_verifying_certifi_ssl_context() -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, *, timeout, context):
        captured.update({"request": request, "timeout": timeout, "context": context})
        return _Response()

    with patch("stock_agent.providers.twelve_data.urllib.request.urlopen", side_effect=fake_urlopen):
        assert _urllib_get_json("https://api.twelvedata.com/time_series", {"symbol": "QQQ"}, 12) == {"values": []}

    context = captured["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert captured["timeout"] == 12


def test_twelve_provider_refuses_a_second_request_after_its_minute_budget_is_spent() -> None:
    calls: list[dict[str, str]] = []

    def fake_get(_url: str, params: dict[str, str], _timeout: float) -> dict[str, object]:
        calls.append(params)
        return {
            "values": [
                {
                    "datetime": "2026-07-14 14:00:00",
                    "open": "1",
                    "high": "2",
                    "low": "0.5",
                    "close": "1.5",
                    "volume": "10",
                }
            ]
        }

    provider = TwelveDataProvider(
        api_key="test-key",
        credit_budget_per_minute=1,
        http_get=fake_get,
        clock_fn=lambda: 100.0,
    )

    provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")
    with pytest.raises(TwelveDataProviderError, match="minute credit budget exhausted"):
        provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")

    assert len(calls) == 1


def test_twelve_provider_default_does_not_retry_a_rate_limited_http_request() -> None:
    calls = 0

    def rate_limited(_url: str, _params: dict[str, str], _timeout: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise TwelveDataProviderError("HTTP Error 429: Too Many Requests")

    provider = TwelveDataProvider(api_key="test-key", http_get=rate_limited)

    with pytest.raises(TwelveDataProviderError, match="429"):
        provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m")

    assert calls == 1
