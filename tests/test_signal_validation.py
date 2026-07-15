from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.bars.validation import generate_bar_id
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.signals import SignalFeature, SignalProposal
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.schemas import Bar
from stock_agent.signal_lab.candidate_builder import CandidateBuildInput, CandidateBuilder
from stock_agent.signal_lab.feature_catalog import DEFAULT_FEATURE_CATALOG
from stock_agent.signal_lab.validation import SignalValidationInput, SignalValidator, ValidationPolicy
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 7, 7, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(from_ts=NOW - timedelta(days=20), to_ts=NOW, timezone="America/New_York")


class ScriptedModel:
    def __init__(self, source: str) -> None:
        self.source = source

    def __call__(self, _prompt: str) -> str:
        return json.dumps(
            {
                "interface_version": "signal_context_v1",
                "required_features": ["return_change"],
                "source_code": self.source,
            }
        )


def test_validation_uses_chronological_splits_and_persists_exploratory_metrics(tmp_path: Path) -> None:
    connection, service, candidate, dataset = _candidate_and_dataset(tmp_path, _stable_source())
    validation_input = _validation_input(candidate, dataset)

    result = SignalValidator(artifact_service=service).validate(
        "task-validation", validation_input, validation_id="validation-stable", now=NOW
    )

    assert result.decision == "pass"
    assert [item.split_name for item in result.split_results] == ["discovery", "validation", "holdout"]
    assert all(item.deterministic for item in result.split_results)
    assert all(item.error_rate == 0 for item in result.split_results)
    assert result.metrics_artifact is not None
    assert "exploratory_no_labels" in result.limitations
    metric_payload = service.load_json("task-validation", result.metrics_artifact)
    assert metric_payload["validation_id"] == result.validation_id
    connection.close()


def test_validation_rejects_future_access_and_full_sample_normalization(tmp_path: Path) -> None:
    connection, service, future_candidate, dataset = _candidate_and_dataset(tmp_path, _future_source())
    future = SignalValidator(artifact_service=service).validate(
        "task-validation", _validation_input(future_candidate, dataset), validation_id="validation-future", now=NOW
    )
    connection.close()

    connection, service, normalized_candidate, dataset = _candidate_and_dataset(tmp_path / "normalized", _global_normalization_source())
    normalized = SignalValidator(artifact_service=service).validate(
        "task-validation", _validation_input(normalized_candidate, dataset), validation_id="validation-normalized", now=NOW
    )

    assert future.decision == "reject"
    assert not next(check for check in future.leakage_checks if check.name == "no_look_ahead").passed
    assert normalized.decision == "reject"
    assert not next(check for check in normalized.leakage_checks if check.name == "no_full_sample_normalization").passed
    connection.close()


def test_validation_marks_sandbox_failures_for_revision_and_insufficient_history_for_rejection(tmp_path: Path) -> None:
    connection, service, failing_candidate, dataset = _candidate_and_dataset(tmp_path, _failing_source())
    failing = SignalValidator(artifact_service=service).validate(
        "task-validation", _validation_input(failing_candidate, dataset), validation_id="validation-failing", now=NOW
    )
    connection.close()

    connection, service, short_candidate, short_dataset = _candidate_and_dataset(tmp_path / "short", _stable_source(), bar_count=5)
    short = SignalValidator(artifact_service=service).validate(
        "task-validation", _validation_input(short_candidate, short_dataset), validation_id="validation-short", now=NOW
    )

    assert failing.decision == "revise"
    assert all(item.error_rate > 0 for item in failing.split_results)
    assert short.decision == "reject"
    assert short.metrics_artifact is not None
    assert any(check.name == "sufficient_chronological_data" and not check.passed for check in short.leakage_checks)
    connection.close()


def test_validation_records_label_association_and_can_require_multiple_symbols(tmp_path: Path) -> None:
    connection, service, candidate, dataset = _candidate_and_dataset(tmp_path, _stable_source())
    labels = {f"QQQ|{(NOW - timedelta(days=11 - index)).isoformat().replace('+00:00', 'Z')}": 1.0 for index in range(12)}
    labeled_input = _validation_input(candidate, dataset).model_copy(update={"labels": labels})
    labeled = SignalValidator(artifact_service=service).validate(
        "task-validation", labeled_input, validation_id="validation-labeled", now=NOW
    )
    association = service.load_json("task-validation", labeled.metrics_artifact)["label_association"]
    connection.close()

    connection, service, candidate, dataset = _candidate_and_dataset(tmp_path / "single", _stable_source())
    single_symbol = _validation_input(candidate, dataset).model_copy(
        update={"policy": ValidationPolicy(min_bars_per_split=2, min_coverage=0.1, min_distinct_symbols=2)}
    )
    rejected = SignalValidator(artifact_service=service).validate(
        "task-validation", single_symbol, validation_id="validation-single", now=NOW
    )

    assert association["labeled_observation_count"] > 0
    assert association["mean_label_for_triggered_points"] == 1.0
    assert rejected.decision == "reject"
    assert "insufficient_distinct_symbols_for_policy" in rejected.limitations
    connection.close()

def _candidate_and_dataset(tmp_path: Path, source: str, *, bar_count: int = 12):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(
        AgentTask(
            task_id="task-validation",
            request=ResearchRequest(
                request_id="request-validation",
                question="Validate a candidate signal without trading.",
                symbols=["QQQ"],
                time_window=WINDOW,
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    bars = [_bar(index) for index in range(bar_count)]
    dataset = service.save_json(
        "task-validation",
        kind="bars",
        payload={"bars": [bar.model_dump(mode="json") for bar in bars]},
        source="fixture-bars",
        created_at=NOW,
    )
    evidence = EvidenceService(connection, service.store).create(
        "task-validation",
        artifact=dataset,
        evidence_type="bar",
        source="fixture-bars",
        observed_at=NOW,
        evidence_id="evidence-bars",
    )
    proposal = SignalProposal(
        proposal_id="proposal-validation",
        hypothesis="Positive return is an exploratory research condition.",
        features=[SignalFeature(name="return_change", source="market", description="Verified return feature.")],
        logic_spec="return_change is positive",
        expected_behavior="Creates a non-trading research observation.",
        invalidation_conditions=["insufficient history"],
        minimum_history_bars=2,
        applicable_symbols=["QQQ"],
        evidence_refs=[evidence],
    )
    candidate = CandidateBuilder(model_client=ScriptedModel(source), artifact_service=service).build(
        "task-validation",
        CandidateBuildInput(
            proposal=proposal,
            feature_catalog=DEFAULT_FEATURE_CATALOG,
            history_artifact=dataset,
            model_id="fixture-model",
        ),
        candidate_id="candidate-validation",
        now=NOW,
    ).candidate
    return connection, service, candidate, dataset


def _validation_input(candidate, dataset):
    return SignalValidationInput(
        candidate=candidate,
        dataset_artifacts=[dataset],
        time_window=WINDOW,
        symbols=["QQQ"],
        policy=ValidationPolicy(min_bars_per_split=2, min_coverage=0.1),
    )


def _bar(index: int) -> Bar:
    timestamp = NOW - timedelta(days=12 - index)
    close = 100 + index
    source = "fixture-validation"
    return Bar(
        bar_id=generate_bar_id("QQQ", "1d", timestamp.isoformat().replace("+00:00", "Z"), source),
        symbol="QQQ",
        timestamp=timestamp,
        interval="1d",
        open=close - 0.2,
        high=close + 0.2,
        low=close - 0.3,
        close=close,
        volume=1_000 + index,
        source=source,
    )


def _stable_source() -> str:
    return (
        "def compute(context):\n"
        "    values = context.features['return_change']\n"
        "    points = []\n"
        "    for timestamp, value in zip(context.timestamps, values):\n"
        "        if value > 0:\n"
        "            points.append({'timestamp': timestamp, 'label': 'positive', 'strength': value, 'confidence': 0.5, 'reason': 'positive return'})\n"
        "    return points\n"
    )


def _future_source() -> str:
    return (
        "def compute(context):\n"
        "    values = context.features['return_change']\n"
        "    points = []\n"
        "    for index in range(len(values) - 1):\n"
        "        future = values[index + 1]\n"
        "        if future > 0:\n"
        "            points.append({'timestamp': context.timestamps[index], 'label': 'positive', 'strength': future, 'confidence': 0.5, 'reason': 'future'})\n"
        "    return points\n"
    )


def _global_normalization_source() -> str:
    return (
        "def compute(context):\n"
        "    values = context.features['return_change']\n"
        "    average = sum(values) / len(values)\n"
        "    return []\n"
    )


def _failing_source() -> str:
    return "def compute(context):\n    values = context.features['return_change']\n    return [1 / 0]\n"
