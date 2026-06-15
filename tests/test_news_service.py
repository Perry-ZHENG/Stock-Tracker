import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from stock_agent.config import validate_config
from stock_agent.news import NewsQueryService, StaticNewsProvider
from stock_agent.schemas import NewsItem
from stock_agent.storage.repositories import list_news_items
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.tracing import utc_now


class NewsServiceTests(unittest.TestCase):
    def test_missing_provider_returns_readable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            connection = initialize_runtime_database(Path(tmp_dir))
            config = validate_config(_news_config(api_key_env="NEWS_API_KEY")).news

            result = NewsQueryService(connection, config=config, provider=None).query(
                symbols=["QQQ"],
                limit=5,
            )
            connection.close()

        self.assertFalse(result.ok)
        self.assertIn("provider placeholder is not configured", result.message)

    def test_missing_api_key_returns_readable_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {}, clear=True):
            connection = initialize_runtime_database(Path(tmp_dir))
            config = validate_config(_news_config(api_key_env="NEWS_API_KEY")).news

            result = NewsQueryService(
                connection,
                config=config,
                provider=StaticNewsProvider([_news_item()]),
            ).query(symbols=["QQQ"], limit=5)
            connection.close()

        self.assertFalse(result.ok)
        self.assertIn("missing api key env NEWS_API_KEY", result.message)

    def test_fetch_writes_news_items_with_original_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"NEWS_API_KEY": "test-key"}):
            connection = initialize_runtime_database(Path(tmp_dir))
            config = validate_config(_news_config(api_key_env="NEWS_API_KEY")).news

            result = NewsQueryService(
                connection,
                config=config,
                provider=StaticNewsProvider([_news_item()]),
            ).query(symbols=["QQQ"], limit=5)
            stored = list_news_items(connection)
            connection.close()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "news_status=fetched rows=1")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["url"], "https://example.com/qqq-news")

    def test_cache_ttl_hit_skips_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"NEWS_API_KEY": "test-key"}):
            connection = initialize_runtime_database(Path(tmp_dir))
            config = validate_config(_news_config(api_key_env="NEWS_API_KEY", cache_ttl_minutes=60)).news
            service = NewsQueryService(
                connection,
                config=config,
                provider=CountingNewsProvider([_news_item()]),
            )

            first = service.query(symbols=["QQQ"], limit=5)
            second = service.query(symbols=["QQQ"], limit=5)
            connection.close()

        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(service.provider.calls, 1)  # type: ignore[attr-defined]

    def test_expired_cache_fetches_again(self) -> None:
        old_item = _news_item(created_at=utc_now() - timedelta(minutes=120))
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"NEWS_API_KEY": "test-key"}):
            connection = initialize_runtime_database(Path(tmp_dir))
            from stock_agent.storage.repositories import insert_news_item

            insert_news_item(connection, old_item)
            config = validate_config(_news_config(api_key_env="NEWS_API_KEY", cache_ttl_minutes=1)).news
            provider = CountingNewsProvider([_news_item(news_id="news-new")])

            result = NewsQueryService(connection, config=config, provider=provider).query(
                symbols=["QQQ"],
                limit=5,
            )
            connection.close()

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "news_status=fetched rows=1")
        self.assertEqual(provider.calls, 1)


class CountingNewsProvider:
    name = "counting"

    def __init__(self, items: list[NewsItem]) -> None:
        self.items = items
        self.calls = 0

    def search(self, *, symbols: list[str], limit: int) -> list[NewsItem]:
        self.calls += 1
        symbol_set = {symbol.upper() for symbol in symbols}
        return [
            item
            for item in self.items
            if item.symbol is None or item.symbol.upper() in symbol_set
        ][:limit]


def _news_config(*, api_key_env: str, cache_ttl_minutes: int = 30):
    from copy import deepcopy
    from stock_agent.config import DEFAULT_CONFIG

    config = deepcopy(DEFAULT_CONFIG)
    config["news"]["api_key_env"] = api_key_env
    config["news"]["cache_ttl_minutes"] = cache_ttl_minutes
    return config


def _news_item(
    *,
    news_id: str = "news-001",
    created_at: datetime | None = None,
) -> NewsItem:
    return NewsItem(
        news_id=news_id,
        symbol="QQQ",
        market="US",
        title="QQQ news",
        summary="A deterministic news item.",
        url="https://example.com/qqq-news",
        source="unit_test",
        published_at=datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        retention_level="raw_summary",
        created_at=created_at or utc_now(),
    )


if __name__ == "__main__":
    unittest.main()
