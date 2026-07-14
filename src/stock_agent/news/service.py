"""On-demand news query service with cache TTL support."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from stock_agent.config import NewsConfig
from stock_agent.news.providers import NewsProvider, is_local_test_provider
from stock_agent.schemas import NewsItem
from stock_agent.storage.repositories import insert_news_item, list_recent_news_items
from stock_agent.tracing import utc_now


@dataclass(frozen=True)
class NewsQueryResult:
    ok: bool
    message: str
    items: list[NewsItem]
    from_cache: bool
    provider_name: str | None = None


class NewsQueryService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        config: NewsConfig,
        provider: NewsProvider | None = None,
        allow_local_provider_without_api_key: bool = False,
    ) -> None:
        self.connection = connection
        self.config = config
        self.provider = provider
        self.allow_local_provider_without_api_key = allow_local_provider_without_api_key

    def query(
        self,
        *,
        symbols: list[str],
        limit: int = 5,
        now: datetime | None = None,
    ) -> NewsQueryResult:
        normalized_symbols = [symbol.upper() for symbol in symbols]
        active_now = now or utc_now()
        if active_now.tzinfo is None:
            raise ValueError("news query time must be timezone-aware")
        active_now = active_now.astimezone(UTC)
        cached_items = list_recent_news_items(
            self.connection,
            symbols=normalized_symbols,
            since=active_now - timedelta(minutes=self.config.cache_ttl_minutes),
            limit=limit,
        )
        if cached_items:
            return NewsQueryResult(
                ok=True,
                message=f"news_status=cache_hit rows={len(cached_items)}",
                items=cached_items,
                from_cache=True,
                provider_name=self.config.provider,
            )

        if not self.config.enabled:
            return NewsQueryResult(
                ok=False,
                message="news_status=disabled reason=news.enabled is false",
                items=[],
                from_cache=False,
                provider_name=self.config.provider,
            )
        if self.provider is None:
            return NewsQueryResult(
                ok=False,
                message=f"news_status=unavailable reason=provider {self.config.provider} is not configured",
                items=[],
                from_cache=False,
                provider_name=self.config.provider,
            )
        if not os.getenv(self.config.api_key_env) and not (
            self.allow_local_provider_without_api_key and is_local_test_provider(self.provider)
        ):
            return NewsQueryResult(
                ok=False,
                message=f"news_status=unavailable reason=missing api key env {self.config.api_key_env}",
                items=[],
                from_cache=False,
                provider_name=self.config.provider,
            )

        items = self.provider.search(symbols=normalized_symbols, limit=limit)
        for item in items:
            insert_news_item(self.connection, item)
        return NewsQueryResult(
            ok=True,
            message=f"news_status=fetched rows={len(items)}",
            items=items,
            from_cache=False,
            provider_name=self.provider.name,
        )


__all__ = ["NewsQueryResult", "NewsQueryService"]
