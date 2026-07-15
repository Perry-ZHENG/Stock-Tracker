from __future__ import annotations

from datetime import UTC, datetime

from stock_agent.providers.synthetic_demo_v2 import SyntheticDemoProviderV2


def test_synthetic_demo_provider_generates_deterministic_regular_session_bars() -> None:
    provider = SyntheticDemoProviderV2()
    start = datetime(2026, 5, 22, 13, 30, tzinfo=UTC)
    end = datetime(2026, 5, 22, 20, 0, tzinfo=UTC)

    first = provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m", start=start, end=end)
    second = provider.fetch_intraday_bars(symbols=["QQQ"], interval="30m", start=start, end=end)

    assert first == second
    assert len(first) == 13
    assert all(bar.source == "synthetic_demo" for bar in first)
    assert all(start <= bar.timestamp <= end for bar in first)
