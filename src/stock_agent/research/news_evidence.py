"""Evidence-first handling of external news with no authority to issue instructions."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.contracts.common import TrustLevel
from stock_agent.contracts.evidence import EvidenceRef, NewsCoverage, NewsEvidence, NewsEvidenceRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.news.deduplication import canonicalize_url, cluster_news_items, deduplicate_news
from stock_agent.news.service import NewsQueryService
from stock_agent.schemas import NewsItem
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.tracing import utc_now


class NewsEvidenceWorkflow:
    """Persist external news as untrusted evidence before any Agent can use it."""

    def __init__(
        self,
        *,
        root: Path,
        connection: sqlite3.Connection,
        query_service: NewsQueryService,
        config_context: RuntimeConfigContext | None = None,
        artifact_service: ArtifactService | None = None,
        source_trust: dict[str, TrustLevel] | None = None,
    ) -> None:
        self.root = root
        self.connection = connection
        self.query_service = query_service
        self.config_context = config_context or load_config(root)
        self.artifact_service = artifact_service or ArtifactService(
            ArtifactStore(connection, root / self.config_context.config.storage.parquet_root)
        )
        self.evidence_service = EvidenceService(connection, self.artifact_service.store)
        self.safety_policy = ResearchSafetyPolicy(connection)
        self.source_trust = {key.casefold(): value for key, value in (source_trust or {}).items()}

    def collect(
        self,
        task_id: str,
        request: NewsEvidenceRequest,
        *,
        now: datetime | None = None,
    ) -> NewsEvidence:
        active_now = _utc_now(now)
        result = self.query_service.query(symbols=request.symbols, limit=request.limit, now=active_now)
        if not result.ok:
            return _empty_evidence(request, conflicts=["news_provider_unavailable"])

        filtered = [item for item in result.items if _matches_request(item, request)]
        unique, conflicts = deduplicate_news(filtered)
        artifact_refs = []
        evidence_refs: list[EvidenceRef] = []
        safe_items: list[NewsItem] = []
        safe_evidence_by_id: dict[str, EvidenceRef] = {}
        for item in unique:
            canonical_url = canonicalize_url(item.url)
            assert canonical_url is not None  # validated by deduplicate_news
            source = f"news:{item.source}"
            trust = self.source_trust.get(item.source.casefold(), "medium")
            safety = self.safety_policy.inspect(
                SafetyRequest(
                    source="news",
                    actor_type="tool",
                    requested_capability="read_news",
                    input_trust="untrusted",
                    untrusted_text=f"{item.title}\n{item.summary}",
                    tool_name="news_evidence",
                    details={"news_id": item.news_id, "canonical_url": canonical_url},
                )
            )
            if not safety.allowed:
                conflicts.append(f"untrusted_instruction:{item.news_id}")
                trust = "low"
            if trust == "low":
                conflicts.append(f"low_trust_source:{item.news_id}")
            artifact = self.artifact_service.save_json(
                task_id,
                kind="news_body",
                payload={
                    "schema_version": "news-evidence-v2",
                    "news_id": item.news_id,
                    "symbol": item.symbol,
                    "title": item.title,
                    "summary": item.summary,
                    "url": item.url,
                    "canonical_url": canonical_url,
                    "source": item.source,
                    "published_at": item.published_at.isoformat().replace("+00:00", "Z"),
                },
                source=source,
                created_at=active_now,
            )
            evidence = self._get_or_create_evidence(
                task_id,
                artifact=artifact,
                source=source,
                observed_at=item.published_at,
                trust_level=trust,
            )
            artifact_refs.append(artifact)
            evidence_refs.append(evidence)
            if safety.allowed and trust != "low":
                safe_items.append(item)
                safe_evidence_by_id[item.news_id] = evidence

        clusters = cluster_news_items(safe_items, safe_evidence_by_id)
        covered_symbols = {
            item.symbol.upper()
            for item in safe_items
            if item.symbol is not None and item.symbol.upper() in request.symbols
        }
        return NewsEvidence(
            request=request,
            clusters=clusters,
            source_count=len({item.source for item in safe_items}),
            coverage=NewsCoverage(
                requested_symbol_count=len(request.symbols),
                covered_symbol_count=len(covered_symbols),
                source_count=len({item.source for item in safe_items}),
            ),
            conflicts=sorted(dict.fromkeys(conflicts)),
            artifact_refs=artifact_refs,
            evidence_refs=evidence_refs,
        )

    def _get_or_create_evidence(
        self,
        task_id: str,
        *,
        artifact: object,
        source: str,
        observed_at: datetime,
        trust_level: TrustLevel,
    ) -> EvidenceRef:
        assert hasattr(artifact, "artifact_id")
        evidence_id = _evidence_id(task_id, artifact.artifact_id)
        existing = TaskRepository(self.connection).get_evidence(task_id, evidence_id)
        if existing is not None:
            return existing
        return self.evidence_service.create(
            task_id,
            artifact=artifact,  # type: ignore[arg-type]
            evidence_type="news",
            source=source,
            observed_at=observed_at,
            trust_level=trust_level,
            evidence_id=evidence_id,
        )


def _matches_request(item: NewsItem, request: NewsEvidenceRequest) -> bool:
    if not (request.time_window.from_ts <= item.published_at <= request.time_window.to_ts):
        return False
    if request.symbols and item.symbol is not None and item.symbol.upper() not in request.symbols:
        return False
    if not request.topics:
        return True
    haystack = f"{item.title}\n{item.summary}".casefold()
    return any(topic.casefold() in haystack for topic in request.topics)


def _empty_evidence(request: NewsEvidenceRequest, *, conflicts: list[str]) -> NewsEvidence:
    return NewsEvidence(
        request=request,
        source_count=0,
        coverage=NewsCoverage(
            requested_symbol_count=len(request.symbols),
            covered_symbol_count=0,
            source_count=0,
        ),
        conflicts=conflicts,
    )


def _evidence_id(task_id: str, artifact_id: str) -> str:
    digest = hashlib.sha256(f"{task_id}|{artifact_id}|news".encode("utf-8")).hexdigest()[:20]
    return f"evidence-news-{digest}"


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or utc_now()
    if active_now.tzinfo is None:
        raise ValueError("workflow time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["NewsEvidenceWorkflow"]
