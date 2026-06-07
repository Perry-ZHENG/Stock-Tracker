"""On-demand news query interfaces."""

from stock_agent.news.providers import NewsProvider, StaticNewsProvider
from stock_agent.news.service import NewsQueryResult, NewsQueryService

__all__ = ["NewsProvider", "NewsQueryResult", "NewsQueryService", "StaticNewsProvider"]
