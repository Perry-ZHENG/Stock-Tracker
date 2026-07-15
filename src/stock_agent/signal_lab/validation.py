"""Time-series validation that reaches generated code only through CandidateSandbox."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from pydantic import Field, model_validator

from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.common import StrictSchema, TimeWindow
from stock_agent.contracts.evidence import ArtifactRef
from stock_agent.contracts.signals import CandidateFunction, SignalValidationResult, StabilityResult, ValidationSplitResult
from stock_agent.schemas import Bar
from stock_agent.signal_lab.leakage import inspect_leakage
from stock_agent.signal_lab.metrics import RunMetrics, cross_symbol_consistency
from stock_agent.signal_lab.sandbox import CandidateSandbox
from stock_agent.signal_lab.splits import TimeSplit, TimeSplitError, split_chronologically
from stock_agent.signal_lab.interface import SignalContext
from stock_agent.storage.signal_repository import SignalRepository


class ValidationPolicy(StrictSchema):
    version: str = Field(default="validation-v1", min_length=1)
    discovery_fraction: float = Field(default=0.5, gt=0, lt=1)
    validation_fraction: float = Field(default=0.25, gt=0, lt=1)
    min_bars_per_split: int = Field(default=2, ge=2, le=10_000)
    min_coverage: float = Field(default=0.01, ge=0, le=1)
    max_error_rate: float = Field(default=0.1, ge=0, le=1)
    min_cross_symbol_consistency: float = Field(default=0.2, ge=0, le=1)
    min_distinct_symbols: int = Field(default=1, ge=1, le=100)
    repeat_runs: int = Field(default=2, ge=2, le=5)

    @model_validator(mode="after")
    def _validate_fractions(self) -> "ValidationPolicy":
        if self.discovery_fraction + self.validation_fraction >= 1:
            raise ValueError("discovery_fraction + validation_fraction must leave a holdout period")
        return self


class SignalValidationInput(StrictSchema):
    candidate: CandidateFunction
    dataset_artifacts: list[ArtifactRef] = Field(min_length=1)
    time_window: TimeWindow
    policy: ValidationPolicy = Field(default_factory=ValidationPolicy)
    symbols: list[str] = Field(min_length=1)
    labels: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_datasets(self) -> "SignalValidationInput":
        if any(reference.kind != "bars" for reference in self.dataset_artifacts):
            raise ValueError("SignalValidationInput dataset_artifacts must contain only bars artifacts")
        normalized = [symbol.upper() for symbol in self.symbols]
        if len(normalized) != len(set(normalized)):
            raise ValueError("validation symbols must be unique")
        self.symbols = normalized
        return self


class SignalValidationError(RuntimeError):
    """Raised for corrupted task artifacts that cannot yield an auditable validation record."""


class SignalValidator:
    def __init__(
        self,
        *,
        artifact_service: ArtifactService,
        sandbox: CandidateSandbox | None = None,
        repository: SignalRepository | None = None,
    ) -> None:
        self.artifact_service = artifact_service
        self.repository = repository or SignalRepository(artifact_service.store.connection)
        self.sandbox = sandbox or CandidateSandbox(artifact_service=artifact_service, repository=self.repository)

    def validate(
        self,
        task_id: str,
        validation_input: SignalValidationInput,
        *,
        validation_id: str,
        now: datetime | None = None,
    ) -> SignalValidationResult:
        active_now = _utc_now(now)
        bars = self._load_bars(task_id, validation_input)
        source = self.artifact_service.open_bytes(task_id, validation_input.candidate.source_artifact).decode("utf-8")
        leakage_checks = inspect_leakage(source)
        limitations: list[str] = []
        if not validation_input.labels:
            limitations.append("exploratory_no_labels")
        if len({bar.symbol for bar in bars}) < validation_input.policy.min_distinct_symbols:
            limitations.append("insufficient_distinct_symbols_for_policy")
            result = self._insufficient_result(validation_input, validation_id, leakage_checks, limitations)
            self._persist(result, task_id, active_now, metrics={"reason": limitations[-1]})
            return self.repository.get_validation(validation_id) or result
        try:
            splits = split_chronologically(
                bars,
                discovery_fraction=validation_input.policy.discovery_fraction,
                validation_fraction=validation_input.policy.validation_fraction,
                min_bars_per_split=validation_input.policy.min_bars_per_split,
                timezone=validation_input.time_window.timezone,
            )
        except TimeSplitError as exc:
            limitations.append(str(exc))
            result = self._insufficient_result(validation_input, validation_id, leakage_checks, limitations)
            self._persist(result, task_id, active_now, metrics={"reason": str(exc)})
            return self.repository.get_validation(validation_id) or result

        split_results: list[ValidationSplitResult] = []
        per_symbol: dict[str, int] = defaultdict(int)
        labeled_values: list[float] = []
        metrics_payload: dict[str, object] = {"policy": validation_input.policy.model_dump(mode="json"), "splits": {}}
        for split in splits:
            metrics, symbol_counts, labels = self._run_split(task_id, validation_input, split, now=active_now)
            for symbol, count in symbol_counts.items():
                per_symbol[symbol] += count
            labeled_values.extend(labels)
            split_results.append(
                ValidationSplitResult(
                    split_name=split.name,
                    time_window=split.time_window,
                    sample_count=metrics.sample_count,
                    observation_count=metrics.observation_count,
                    deterministic=metrics.deterministic,
                    error_rate=metrics.error_rate,
                )
            )
            metrics_payload["splits"][split.name] = {
                "coverage": metrics.coverage,
                "error_count": metrics.error_count,
                "symbol_observations": symbol_counts,
            }
        metrics_payload["label_association"] = (
            {
                "labeled_observation_count": len(labeled_values),
                "mean_label_for_triggered_points": sum(labeled_values) / len(labeled_values),
            }
            if labeled_values
            else {"status": "exploratory_no_labels"}
        )
        coverage = sum(item.observation_count for item in split_results) / sum(item.sample_count for item in split_results)
        determinism = all(item.deterministic for item in split_results)
        errors = max((item.error_rate for item in split_results), default=1.0)
        consistency = cross_symbol_consistency(dict(per_symbol))
        stability_passed = (
            determinism
            and coverage >= validation_input.policy.min_coverage
            and errors <= validation_input.policy.max_error_rate
            and (consistency is None or consistency >= validation_input.policy.min_cross_symbol_consistency)
        )
        stability = StabilityResult(
            passed=stability_passed,
            coverage=coverage,
            cross_symbol_consistency=consistency,
            notes=[] if stability_passed else ["coverage, error rate, determinism, or cross-symbol consistency missed policy"],
        )
        decision = "pass" if stability_passed and all(check.passed for check in leakage_checks) else "reject" if not all(check.passed for check in leakage_checks) else "revise"
        result = SignalValidationResult(
            validation_id=validation_id,
            candidate_id=validation_input.candidate.candidate_id,
            dataset_refs=validation_input.dataset_artifacts,
            split_results=split_results,
            leakage_checks=leakage_checks,
            stability=stability,
            limitations=limitations,
            decision=decision,
        )
        self._persist(result, task_id, active_now, metrics=metrics_payload)
        return self.repository.get_validation(validation_id) or result

    def _load_bars(self, task_id: str, validation_input: SignalValidationInput) -> list[Bar]:
        bars: list[Bar] = []
        try:
            for artifact in validation_input.dataset_artifacts:
                payload = self.artifact_service.load_json(task_id, artifact)
                if not isinstance(payload, dict) or not isinstance(payload.get("bars"), list):
                    raise SignalValidationError("validation dataset artifact has no bars array")
                bars.extend(Bar.model_validate(item) for item in payload["bars"])
        except Exception as exc:
            raise SignalValidationError("validation dataset artifact is unavailable or invalid") from exc
        selected = [
            bar
            for bar in bars
            if bar.symbol in validation_input.symbols
            and validation_input.time_window.from_ts <= bar.timestamp <= validation_input.time_window.to_ts
        ]
        if not selected:
            raise SignalValidationError("validation dataset has no bars in the requested symbol/time scope")
        return selected

    def _run_split(
        self,
        task_id: str,
        validation_input: SignalValidationInput,
        split: TimeSplit,
        *,
        now: datetime,
    ) -> tuple[RunMetrics, dict[str, int], list[float]]:
        by_symbol: dict[str, list[Bar]] = defaultdict(list)
        for bar in split.bars:
            by_symbol[bar.symbol].append(bar)
        observations = 0
        errors = 0
        deterministic = True
        symbol_counts: dict[str, int] = {}
        label_values: list[float] = []
        for symbol, symbol_bars in by_symbol.items():
            context = _context_for_bars(validation_input.candidate.candidate_id, symbol, symbol_bars, self.repository)
            context_artifact = self.artifact_service.save_json(
                task_id,
                kind="validation_metrics",
                payload=context.model_dump(mode="json"),
                source="signal_validation:context",
                created_at=now,
            )
            runs = [self.sandbox.run(task_id, validation_input.candidate, context_artifact, now=now) for _ in range(validation_input.policy.repeat_runs)]
            if any(run.status != "succeeded" for run in runs):
                errors += 1
                deterministic = False
                symbol_counts[symbol] = 0
                continue
            artifacts = [run.point_artifact for run in runs]
            if len({artifact.sha256 for artifact in artifacts if artifact is not None}) != 1:
                deterministic = False
            count = runs[0].point_count
            observations += count
            symbol_counts[symbol] = count
            if validation_input.labels and runs[0].point_artifact is not None:
                payload = self.artifact_service.load_json(task_id, runs[0].point_artifact)
                for point in payload.get("points", []):
                    if not isinstance(point, dict) or not isinstance(point.get("timestamp"), str):
                        continue
                    key = f"{symbol}|{point['timestamp']}"
                    if key in validation_input.labels:
                        label_values.append(validation_input.labels[key])
        return (
            RunMetrics(
                sample_count=len(split.bars),
                observation_count=observations,
                error_count=errors,
                deterministic=deterministic,
            ),
            symbol_counts,
            label_values,
        )

    def _insufficient_result(
        self,
        validation_input: SignalValidationInput,
        validation_id: str,
        leakage_checks,
        limitations: list[str],
    ) -> SignalValidationResult:
        return SignalValidationResult(
            validation_id=validation_id,
            candidate_id=validation_input.candidate.candidate_id,
            dataset_refs=validation_input.dataset_artifacts,
            split_results=[
                ValidationSplitResult(
                    split_name="discovery",
                    time_window=validation_input.time_window,
                    sample_count=0,
                    observation_count=0,
                    deterministic=False,
                    error_rate=1.0,
                )
            ],
            leakage_checks=[*leakage_checks, _insufficient_data_check(limitations[-1])],
            stability=StabilityResult(passed=False, coverage=0, notes=["insufficient chronological data"]),
            limitations=limitations,
            decision="reject",
        )

    def _persist(self, result: SignalValidationResult, task_id: str, now: datetime, *, metrics: dict[str, object]) -> None:
        artifact = self.artifact_service.save_json(
            task_id,
            kind="validation_metrics",
            payload={"validation_id": result.validation_id, "candidate_id": result.candidate_id, **metrics},
            source="signal_validation:metrics",
            created_at=now,
        )
        persisted = result.model_copy(update={"metrics_artifact": artifact})
        self.repository.save_validation(persisted, created_at=now)


def _context_for_bars(candidate_id: str, symbol: str, bars: list[Bar], repository: SignalRepository) -> SignalContext:
    provenance = repository.get_build_provenance(candidate_id)
    if provenance is None:
        raise SignalValidationError("candidate provenance is unavailable")
    ordered = sorted(bars, key=lambda bar: bar.timestamp)
    catalog_features = provenance.feature_catalog.names
    values = _feature_arrays(ordered, catalog_features)
    return SignalContext(
        catalog_version=provenance.feature_catalog.version,
        symbol=symbol,
        timestamps=tuple(bar.timestamp for bar in ordered),
        features=values,
    )


def _feature_arrays(bars: list[Bar], feature_names: set[str]) -> dict[str, tuple[float, ...]]:
    arrays: dict[str, list[float]] = {name: [] for name in feature_names}
    for index, bar in enumerate(bars):
        previous = bars[index - 1] if index else None
        baseline = bars[max(0, index - 3) : index]
        values = {
            "return_change": (bar.close / previous.close - 1) if previous is not None and previous.close else 0.0,
            "gap": (bar.open / previous.close - 1) if previous is not None and previous.close else 0.0,
            "volume_ratio": (bar.volume / (sum(item.volume for item in baseline) / len(baseline))) if baseline and sum(item.volume for item in baseline) else 0.0,
            "relative_to_baseline": (bar.close / (sum(item.close for item in baseline) / len(baseline)) - 1) if baseline and sum(item.close for item in baseline) else 0.0,
            "realized_volatility": 0.0,
        }
        for name in arrays:
            arrays[name].append(float(values[name]))
    return {name: tuple(values) for name, values in arrays.items()}


def _insufficient_data_check(reason: str):
    from stock_agent.contracts.signals import LeakageCheck

    return LeakageCheck(name="sufficient_chronological_data", passed=False, details=reason)


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("validation time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["SignalValidationError", "SignalValidationInput", "SignalValidator", "ValidationPolicy"]
