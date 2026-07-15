"""Run only approved registry versions through CandidateSandbox; never call a model."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Literal

from pydantic import Field

from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.evidence import DataEvidence, EvidenceRef
from stock_agent.contracts.signals import SignalObservation
from stock_agent.evidence.service import EvidenceService
from stock_agent.schemas import Bar
from stock_agent.signal_lab.interface import SignalContext
from stock_agent.signal_lab.sandbox import CandidateSandbox
from stock_agent.storage.signal_repository import SignalRepository


class RunnerPolicy(StrictSchema):
    mode: Literal["legacy", "registry", "hybrid"] = "hybrid"


class SignalRunTrace(StrictSchema):
    signal_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: Literal["succeeded", "isolated", "skipped"]
    reason: str | None = None


class ActiveSignalRunResult(StrictSchema):
    observations: list[SignalObservation] = Field(default_factory=list)
    traces: list[SignalRunTrace] = Field(default_factory=list)
    active_version_count: int = Field(ge=0)


class ActiveSignalRunner:
    def __init__(self, *, artifact_service: ArtifactService, repository: SignalRepository | None = None, sandbox: CandidateSandbox | None = None) -> None:
        self.artifact_service = artifact_service
        self.repository = repository or SignalRepository(artifact_service.store.connection)
        self.sandbox = sandbox or CandidateSandbox(artifact_service=artifact_service, repository=self.repository)

    def run(self, task_id: str, data_evidence: DataEvidence, *, now: datetime | None = None) -> ActiveSignalRunResult:
        active_now = _utc_now(now)
        if not self._verify_data(task_id, data_evidence, now=active_now):
            return ActiveSignalRunResult(active_version_count=0, traces=[])
        bars = _bars(self.artifact_service.load_json(task_id, data_evidence.bar_artifact))
        versions = self.repository.list_active_versions()
        observations: list[SignalObservation] = []
        traces: list[SignalRunTrace] = []
        for version in versions:
            candidate = self.repository.get_candidate_by_source_hash(version.source_hash)
            provenance = candidate and self.repository.get_build_provenance(candidate.candidate_id)
            if candidate is None or provenance is None or candidate.source_hash != version.source_hash:
                traces.append(SignalRunTrace(signal_id=version.signal_id, version=version.version, status="isolated", reason="candidate hash mismatch"))
                continue
            by_symbol: dict[str, list[Bar]] = defaultdict(list)
            for bar in bars:
                if bar.symbol in provenance.proposal.applicable_symbols:
                    by_symbol[bar.symbol].append(bar)
            for symbol, rows in by_symbol.items():
                context = _context(symbol, rows, provenance.feature_catalog.names, provenance.feature_catalog.version)
                context_artifact = self.artifact_service.save_json(task_id, kind="validation_metrics", payload=context.model_dump(mode="json"), source="active_signal_runner:context", created_at=active_now)
                outcome = self.sandbox.run(task_id, candidate, context_artifact, now=active_now)
                if outcome.status != "succeeded" or outcome.point_artifact is None:
                    traces.append(SignalRunTrace(signal_id=version.signal_id, version=version.version, status="isolated", reason=outcome.reason))
                    continue
                payload = self.artifact_service.load_json(task_id, outcome.point_artifact)
                for point in payload.get("points", []):
                    observation = SignalObservation(signal_id=version.signal_id, version=version.version, symbol=symbol, evidence_refs=data_evidence.evidence_refs, **point)
                    self.repository.append_observation(observation)
                    observations.append(observation)
                traces.append(SignalRunTrace(signal_id=version.signal_id, version=version.version, status="succeeded"))
        return ActiveSignalRunResult(observations=observations, traces=traces, active_version_count=len(versions))

    def _verify_data(self, task_id: str, data: DataEvidence, *, now: datetime) -> bool:
        try:
            evidence = EvidenceService(self.artifact_service.store.connection, self.artifact_service.store)
            if [evidence.get(task_id, ref.evidence_id, now=now) for ref in data.evidence_refs] != data.evidence_refs:
                return False
            self.artifact_service.open_bytes(task_id, data.bar_artifact)
            return data.quality.status == "normal"
        except Exception:
            return False


def _bars(payload: object) -> list[Bar]:
    if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list):
        return []
    return [Bar.model_validate(value) for value in payload["bars"]]


def _context(symbol: str, bars: list[Bar], features: set[str], version: str) -> SignalContext:
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    arrays = {name: [] for name in features}
    for index, bar in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        baseline = ordered[max(0, index - 3):index]
        values = {
            "return_change": bar.close / previous.close - 1 if previous and previous.close else 0.0,
            "gap": bar.open / previous.close - 1 if previous and previous.close else 0.0,
            "volume_ratio": bar.volume / (sum(item.volume for item in baseline) / len(baseline)) if baseline else 0.0,
            "relative_to_baseline": bar.close / (sum(item.close for item in baseline) / len(baseline)) - 1 if baseline else 0.0,
            "realized_volatility": 0.0,
        }
        for name in arrays:
            arrays[name].append(values[name])
    return SignalContext(catalog_version=version, symbol=symbol, timestamps=tuple(bar.timestamp for bar in ordered), features={name: tuple(values) for name, values in arrays.items()})


def _utc_now(value: datetime | None) -> datetime:
    active = value or datetime.now(UTC)
    if active.tzinfo is None:
        raise ValueError("runner time must be timezone-aware")
    return active.astimezone(UTC)


__all__ = ["ActiveSignalRunResult", "ActiveSignalRunner", "RunnerPolicy", "SignalRunTrace"]
