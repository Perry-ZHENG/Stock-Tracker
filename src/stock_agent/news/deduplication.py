"""Deterministic URL/title de-duplication and conservative event clustering."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from stock_agent.contracts.evidence import EvidenceRef, NewsCluster
from stock_agent.schemas import NewsItem

_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"})
_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.IGNORECASE)


def canonicalize_url(url: str) -> str | None:
    """Drop fragments and common tracking parameters without fetching the URL."""

    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
    ]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/", urlencode(sorted(query)), ""))


def title_fingerprint(title: str) -> str:
    normalized = " ".join(_title_tokens(title))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def deduplicate_news(items: list[NewsItem]) -> tuple[list[NewsItem], list[str]]:
    """Prefer the earliest stable item for canonical URL and title duplicates."""

    unique: list[NewsItem] = []
    conflicts: list[str] = []
    by_url: dict[str, NewsItem] = {}
    by_title: dict[str, NewsItem] = {}
    for item in sorted(items, key=lambda value: (value.published_at, value.news_id)):
        canonical = canonicalize_url(item.url)
        if canonical is None:
            conflicts.append(f"invalid_url:{item.news_id}")
            continue
        prior = by_url.get(canonical)
        if prior is not None:
            conflicts.append(f"duplicate_url:{item.news_id}:{prior.news_id}")
            continue
        fingerprint = title_fingerprint(item.title)
        prior = by_title.get(fingerprint)
        if prior is not None:
            conflicts.append(f"duplicate_title:{item.news_id}:{prior.news_id}")
            continue
        by_url[canonical] = item
        by_title[fingerprint] = item
        unique.append(item)
    return unique, conflicts


def cluster_news_items(
    items: list[NewsItem],
    evidence_by_news_id: dict[str, EvidenceRef],
) -> list[NewsCluster]:
    """Cluster only safe, evidence-backed items sharing an entity and event window."""

    grouped: dict[str, list[list[NewsItem]]] = defaultdict(list)
    for item in sorted(items, key=lambda value: (value.symbol or "", value.published_at, value.news_id)):
        entity = (item.symbol or "MARKET").upper()
        placed = False
        for cluster in grouped[entity]:
            representative = cluster[0]
            within_window = abs((item.published_at - representative.published_at).total_seconds()) <= timedelta(hours=24).total_seconds()
            if within_window and _token_similarity(item.title, representative.title) >= 0.4:
                cluster.append(item)
                placed = True
                break
        if not placed:
            grouped[entity].append([item])

    clusters: list[NewsCluster] = []
    for entity in sorted(grouped):
        for items_in_cluster in grouped[entity]:
            ids = sorted(item.news_id for item in items_in_cluster)
            digest = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()[:16]
            representative = min(items_in_cluster, key=lambda item: (item.published_at, item.news_id))
            clusters.append(
                NewsCluster(
                    cluster_id=f"news-cluster-{digest}",
                    headline=representative.title,
                    news_ids=ids,
                    evidence_refs=[evidence_by_news_id[item_id] for item_id in ids],
                )
            )
    return sorted(clusters, key=lambda cluster: cluster.cluster_id)


def _title_tokens(value: str) -> list[str]:
    return sorted(set(token.lower() for token in _TOKEN_RE.findall(value)))


def _token_similarity(first: str, second: str) -> float:
    first_tokens = set(_title_tokens(first))
    second_tokens = set(_title_tokens(second))
    if not first_tokens or not second_tokens:
        return 0.0
    return len(first_tokens & second_tokens) / len(first_tokens | second_tokens)


__all__ = ["canonicalize_url", "cluster_news_items", "deduplicate_news", "title_fingerprint"]
