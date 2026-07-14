"""On-demand news query interfaces."""

from stock_agent.news.deduplication import canonicalize_url, cluster_news_items, deduplicate_news
from stock_agent.news.providers import NewsProvider, StaticNewsProvider, is_local_test_provider
from stock_agent.news.service import NewsQueryResult, NewsQueryService

__all__ = [
    "NewsProvider",
    "NewsQueryResult",
    "NewsQueryService",
    "StaticNewsProvider",
    "canonicalize_url",
    "cluster_news_items",
    "deduplicate_news",
    "is_local_test_provider",
]
