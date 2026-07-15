"""Task-scoped, reproducible market-data evidence built from existing providers."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.bars import BarBuilder, quarantine_abnormal_bars
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.evidence import DataEvidence, DataEvidenceRequest, DataQuality, ProviderReference
from stock_agent.evidence.service import EvidenceService
from stock_agent.providers.registry import ProviderFetchResult, ProviderRegistry, ProviderRegistryError
from stock_agent.scheduler.market_calendar import USMarketCalendar
from stock_agent.schemas import Bar
from stock_agent.supervisor.provider_compare import (
    apply_compare_quality,
    compare_provider_bars,
    persist_provider_compare,
)
from stock_agent.tracing import utc_now
from stock_agent.research.features import compute_market_features

V2_READ_ONLY_PROVIDER_NAMES = frozenset({"synthetic_demo", "twelve_data", "twelvedata"})


class DataEvidenceFailure(StrictSchema):
    """A safe, machine-readable result when no usable DataEvidence can be produced."""

    code: Literal["market_closed", "provider_failed", "empty_result", "no_usable_bars", "invalid_request"]
    message: str = Field(min_length=1, max_length=4_000)
    quality: DataQuality
    attempts: list[dict[str, JsonValue]] = Field(default_factory=list)
    retryable: bool = False


class DataEvidenceWorkflowError(RuntimeError):
    """Raised only for programming or persistence boundary failures."""


class DataEvidenceWorkflow:
    """Turn an explicit market-data request into bounded references and summaries.

    The workflow uses the read-only V2 provider registry and deterministic bar
    validation. It never calls a model and never exposes a source path to the
    Agent-facing output.
    """

    def __init__(
        self,
        *,
        root: Path,
        connection: sqlite3.Connection,
        config_context: RuntimeConfigContext | None = None,
        provider_registry: ProviderRegistry | None = None,
        artifact_service: ArtifactService | None = None,
        calendar: USMarketCalendar | None = None,
        comparison_provider_name: str | None = None,
    ) -> None:
        self.root = root
        self.connection = connection
        self.config_context = config_context or load_config(root)
        self.provider_registry = provider_registry or ProviderRegistry(
            root=root,
            config=self.config_context.config,
            connection=connection,
            allowed_provider_names=V2_READ_ONLY_PROVIDER_NAMES,
        )
        self.artifact_service = artifact_service or ArtifactService(
            ArtifactStore(connection, root / self.config_context.config.storage.parquet_root)
        )
        self.evidence_service = EvidenceService(connection, self.artifact_service.store)
        self.calendar = calendar or USMarketCalendar(timezone=self.config_context.config.app.timezone)
        self.comparison_provider_name = comparison_provider_name

    def collect(
        self,
        task_id: str,
        request: DataEvidenceRequest,
        *,
        now: datetime | None = None,
    ) -> DataEvidence | DataEvidenceFailure:
        active_now = _utc_now(now)
        market_failure = self._validate_market_window(request)
        if market_failure is not None:
            return market_failure

        try:
            fetched = self.provider_registry.fetch_intraday_bars(
                symbols=request.symbols,
                interval=request.interval,
                start=request.time_window.from_ts,
                end=request.time_window.to_ts,
            )
        except ProviderRegistryError as exc:
            rate_limited = "credit budget exhausted" in str(exc).casefold()
            return DataEvidenceFailure(
                code="provider_failed",
                message=str(exc),
                quality=DataQuality(status="unavailable", flags=["provider_unavailable"]),
                # Retrying during the same quota window only burns Worker time;
                # preserve the gap for a later, explicit user retry instead.
                retryable=not rate_limited,
            )
        if not fetched.bars:
            return DataEvidenceFailure(
                code="empty_result",
                message="providers returned no bars for the requested window",
                quality=DataQuality(status="unavailable", flags=["empty_provider_result"]),
                attempts=_attempts_payload(fetched),
                retryable=False,
            )
        if fetched.provider_name == "synthetic_demo" and request.freshness_seconds > 0:
            return DataEvidenceFailure(
                code="provider_failed",
                message="synthetic demo data cannot satisfy a current-data request",
                quality=DataQuality(status="unavailable", flags=["synthetic_data_rejected_for_current_request"]),
                attempts=_attempts_payload(fetched),
                retryable=False,
            )

        prepared, rejected = self._prepare_bars(fetched.bars)
        quarantine = quarantine_abnormal_bars(
            prepared,
            expected_interval_minutes=_interval_minutes(request.interval),
        )
        if quarantine.quarantined:
            from stock_agent.bars.quarantine import persist_quarantine_result

            persist_quarantine_result(self.connection, quarantine)
        clean_bars = sorted(quarantine.clean_bars, key=lambda bar: (bar.symbol, bar.timestamp, bar.bar_id))
        if not clean_bars:
            return DataEvidenceFailure(
                code="no_usable_bars",
                message="all returned bars failed validation or quarantine",
                quality=DataQuality(
                    status="unavailable",
                    quarantined_bar_count=len(quarantine.quarantined) + len(rejected),
                    flags=["no_usable_bars"],
                ),
                attempts=_attempts_payload(fetched),
                retryable=False,
            )

        comparison_flags: list[str] = []
        if self.comparison_provider_name:
            clean_bars, comparison_flags = self._compare_provider(
                request,
                primary_provider=fetched.provider_name,
                primary_bars=clean_bars,
            )
        quality_flags = _quality_flags(request, fetched, clean_bars, rejected, quarantine.quarantined, active_now)
        quality_flags.extend(comparison_flags)
        try:
            features, feature_flags = compute_market_features(
                clean_bars,
                requested_features=request.features,
                baseline_window=request.baseline_window,
                source_window=request.time_window,
            )
        except ValueError as exc:
            return DataEvidenceFailure(
                code="invalid_request",
                message=str(exc),
                quality=DataQuality(status="unavailable", flags=["unsupported_feature"]),
            )
        quality_flags.extend(feature_flags)
        quality = DataQuality(
            status="degraded" if quality_flags else "normal",
            missing_bar_count=sum("missing window" in item.reason for item in quarantine.quarantined),
            quarantined_bar_count=len(quarantine.quarantined) + len(rejected),
            flags=sorted(dict.fromkeys(quality_flags)),
        )

        source = fetched.provider_name
        artifact = self.artifact_service.save_json(
            task_id,
            kind="bars",
            payload=_artifact_payload(request, fetched, clean_bars, rejected, quarantine.quarantined),
            source=source,
            created_at=active_now,
        )
        observed_at = max(bar.timestamp for bar in clean_bars)
        # Historical research must remain reproducible after the live-data
        # freshness window has elapsed. Only requests that explicitly carry a
        # positive freshness requirement expire their evidence references.
        valid_until = (
            observed_at + timedelta(seconds=request.freshness_seconds)
            if request.freshness_seconds > 0
            else None
        )
        evidence_refs = [
            self._get_or_create_evidence(
                task_id,
                artifact=artifact,
                evidence_type="bar",
                source=source,
                observed_at=observed_at,
                valid_until=valid_until,
            ),
            self._get_or_create_evidence(
                task_id,
                artifact=artifact,
                evidence_type="provider",
                source=source,
                observed_at=observed_at,
                valid_until=valid_until,
            ),
        ]
        return DataEvidence(
            request=request,
            bar_artifact=artifact,
            summary=_summary(fetched, clean_bars, quality),
            features=features,
            quality=quality,
            provider_refs=[
                ProviderReference(
                    provider_name=fetched.provider_name,
                    request_id=fetched.request_id,
                    observed_at=observed_at,
                    fallback_used=fetched.fallback_used,
                )
            ],
            evidence_refs=evidence_refs,
        )

    def _validate_market_window(self, request: DataEvidenceRequest) -> DataEvidenceFailure | None:
        local_start = request.time_window.from_ts.astimezone(self.calendar.zone).date()
        local_end = request.time_window.to_ts.astimezone(self.calendar.zone).date()
        days = (local_end - local_start).days
        if days > 370:
            return DataEvidenceFailure(
                code="invalid_request",
                message="market-data window must not exceed 370 calendar days",
                quality=DataQuality(status="unavailable", flags=["window_too_large"]),
            )
        if any(self.calendar.market_day(local_start + timedelta(days=offset)).is_trading_day for offset in range(days + 1)):
            return None
        return DataEvidenceFailure(
            code="market_closed",
            message="the requested window contains no US trading day",
            quality=DataQuality(status="unavailable", flags=["market_closed"]),
            retryable=False,
        )

    def _prepare_bars(self, bars: list[Bar]) -> tuple[list[Bar], list[tuple[str, str]]]:
        builder = BarBuilder(regular_session_only=True)
        result = builder.validate_for_evidence(bars)
        return result.valid_bars, result.rejected

    def _compare_provider(
        self,
        request: DataEvidenceRequest,
        *,
        primary_provider: str,
        primary_bars: list[Bar],
    ) -> tuple[list[Bar], list[str]]:
        assert self.comparison_provider_name is not None
        try:
            secondary = self.provider_registry.fetch_from_provider(
                self.comparison_provider_name,
                symbols=request.symbols,
                interval=request.interval,
                start=request.time_window.from_ts,
                end=request.time_window.to_ts,
            )
            secondary_bars, _rejected = self._prepare_bars(secondary.bars)
            result = compare_provider_bars(primary_bars=primary_bars, secondary_bars=secondary_bars)
            persist_provider_compare(
                self.connection,
                result,
                primary_provider=primary_provider,
                secondary_provider=secondary.provider_name,
            )
            flags = [] if result.status in {"ok", "skipped"} else [f"provider_compare_{result.status}"]
            return apply_compare_quality(primary_bars, result), flags
        except ProviderRegistryError:
            return primary_bars, ["provider_compare_unavailable"]

    def _get_or_create_evidence(self, task_id: str, **kwargs: object):
        artifact = kwargs["artifact"]
        evidence_type = kwargs["evidence_type"]
        assert hasattr(artifact, "artifact_id")
        evidence_id = _evidence_id(task_id, artifact.artifact_id, str(evidence_type))
        from stock_agent.storage.task_repository import TaskRepository

        existing = TaskRepository(self.connection).get_evidence(task_id, evidence_id)
        if existing is not None:
            return existing
        return self.evidence_service.create(task_id, evidence_id=evidence_id, **kwargs)  # type: ignore[arg-type]


def _artifact_payload(
    request: DataEvidenceRequest,
    fetched: ProviderFetchResult,
    bars: list[Bar],
    rejected: list[tuple[str, str]],
    quarantined: list[object],
) -> dict[str, object]:
    return {
        "schema_version": "data-evidence-v2",
        "request": request.model_dump(mode="json"),
        "provider": fetched.provider_name,
        "bars": [bar.model_dump(mode="json") for bar in bars],
        "rejected": [{"bar_id": bar_id, "reason": reason} for bar_id, reason in rejected],
        "quarantined": [
            {
                "bar": item.bar.model_dump(mode="json"),
                "reason": item.reason,
                "severity": item.severity,
            }
            for item in quarantined
        ],
    }


def _quality_flags(
    request: DataEvidenceRequest,
    fetched: ProviderFetchResult,
    bars: list[Bar],
    rejected: list[tuple[str, str]],
    quarantined: list[object],
    now: datetime,
) -> list[str]:
    flags: list[str] = []
    received_symbols = {bar.symbol for bar in bars}
    missing_symbols = sorted(set(request.symbols) - received_symbols)
    if missing_symbols:
        flags.append(f"missing_symbols:{','.join(missing_symbols)}")
    if fetched.fallback_used:
        flags.append("provider_fallback_used")
    if fetched.provider_name == "synthetic_demo":
        flags.append("synthetic_demo_data")
    if rejected:
        flags.append(f"invalid_bars:{len(rejected)}")
    if quarantined:
        flags.append(f"quarantined_bars:{len(quarantined)}")
    latest = max(bar.timestamp for bar in bars)
    if request.freshness_seconds > 0 and now - latest > timedelta(seconds=request.freshness_seconds):
        flags.append("stale_data")
    return flags


def _attempts_payload(fetched: ProviderFetchResult) -> list[dict[str, JsonValue]]:
    return [
        {
            "provider_name": attempt.provider_name,
            "status": attempt.status,
            "request_id": attempt.request_id,
            "error_type": attempt.error_type,
            "bar_count": attempt.bar_count,
        }
        for attempt in fetched.attempts
    ]


def _summary(fetched: ProviderFetchResult, bars: list[Bar], quality: DataQuality) -> str:
    symbols = ", ".join(sorted({bar.symbol for bar in bars}))
    return (
        f"{fetched.provider_name} returned {len(bars)} usable bars for {symbols}; "
        f"quality={quality.status}; fallback={'yes' if fetched.fallback_used else 'no'}."
    )


def _evidence_id(task_id: str, artifact_id: str, evidence_type: str) -> str:
    digest = hashlib.sha256(f"{task_id}|{artifact_id}|{evidence_type}".encode("utf-8")).hexdigest()[:20]
    return f"evidence-data-{digest}"


def _interval_minutes(interval: str) -> int | None:
    if not interval.endswith("m"):
        return None
    try:
        return int(interval[:-1])
    except ValueError:
        return None


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or utc_now()
    if active_now.tzinfo is None:
        raise ValueError("workflow time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = [
    "DataEvidenceFailure",
    "DataEvidenceWorkflow",
    "DataEvidenceWorkflowError",
    "V2_READ_ONLY_PROVIDER_NAMES",
]
