from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.signals import CandidateFunction, SignalFeature, SignalProposal
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.signal_lab.candidate_builder import CandidateBuildInput, CandidateBuilder
from stock_agent.signal_lab.feature_catalog import DEFAULT_FEATURE_CATALOG
from stock_agent.signal_lab.interface import CandidateBuildProvenance, SignalContext
from stock_agent.signal_lab.sandbox import CandidateSandbox, SandboxPolicy
from stock_agent.storage.signal_repository import SignalRepository
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 6, 6, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(from_ts=NOW - timedelta(days=10), to_ts=NOW, timezone="America/New_York")


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


def _valid_source() -> str:
    return (
        "def compute(context):\n"
        "    values = context.features['return_change']\n"
        "    points = []\n"
        "    for timestamp, value in zip(context.timestamps, values):\n"
        "        if value > 0:\n"
        "            points.append({'timestamp': timestamp, 'label': 'positive', 'strength': value, 'confidence': 0.5, 'reason': 'positive return'})\n"
        "    return points\n"
    )


def _empty_source() -> str:
    return "def compute(context):\n    values = context.features['return_change']\n    return []\n"


def _zero_division_source() -> str:
    return "def compute(context):\n    values = context.features['return_change']\n    return [1 / 0]\n"


def _non_point_source() -> str:
    return "def compute(context):\n    values = context.features['return_change']\n    return ['not-a-point']\n"


def _infinite_loop_source() -> str:
    return "def compute(context):\n    values = context.features['return_change']\n    while True:\n        pass\n"


def _memory_growth_source() -> str:
    return "def compute(context):\n    values = context.features['return_change']\n    data = [0] * 100000000\n    return []\n"


def test_sandbox_executes_candidate_in_child_and_artifacts_valid_points(tmp_path: Path) -> None:
    connection, service, candidate, context_artifact = _built_candidate(tmp_path, _valid_source())

    result = CandidateSandbox(artifact_service=service).run(
        "task-sandbox", candidate, context_artifact, now=NOW
    )

    assert result.status == "succeeded"
    assert result.point_count == 1
    assert result.point_artifact is not None
    payload = service.load_json("task-sandbox", result.point_artifact)
    assert payload["points"][0]["label"] == "positive"
    connection.close()


@pytest.mark.parametrize(
    ("source", "expected_status"),
    [
        (_empty_source(), "succeeded"),
        (_zero_division_source(), "failed"),
        (_non_point_source(), "failed"),
        (_infinite_loop_source(), "timed_out"),
        (_memory_growth_source(), "resource_limited"),
    ],
)
def test_sandbox_isolates_execution_failures_and_keeps_parent_healthy(
    tmp_path: Path,
    source: str,
    expected_status: str,
) -> None:
    connection, service, candidate, context_artifact = _built_candidate(tmp_path, source)
    sandbox = CandidateSandbox(
        artifact_service=service,
        policy=SandboxPolicy(timeout_seconds=0.3, memory_limit_mb=32),
    )

    result = sandbox.run("task-sandbox", candidate, context_artifact, now=NOW)

    assert result.status == expected_status
    assert result.point_artifact is None or expected_status == "succeeded"
    assert connection.execute("SELECT 1").fetchone()[0] == 1
    connection.close()


@pytest.mark.parametrize(
    "source",
    [
        "def compute(context):\n    values = context.features['return_change']\n    open('host.txt', 'w')\n    return []\n",
        "import os\n\ndef compute(context):\n    return []\n",
        "def compute(context):\n    values = context.features['return_change']\n    return context.__class__\n",
        "def compute(context):\n    values = context.features['return_change']\n    return eval('[]')\n",
        "def compute(context):\n    values = context.features['return_change']\n    return globals()\n",
    ],
)
def test_sandbox_rejects_capability_and_introspection_attacks_before_child_execution(tmp_path: Path, source: str) -> None:
    connection, service, candidate, context_artifact = _raw_candidate(tmp_path, source)
    target = tmp_path / "host.txt"

    result = CandidateSandbox(artifact_service=service).run("task-sandbox", candidate, context_artifact, now=NOW)

    assert result.status == "rejected"
    assert not target.exists()
    connection.close()


def test_sandbox_rejects_candidate_without_builder_provenance(tmp_path: Path) -> None:
    connection, service, candidate, context_artifact = _raw_candidate(tmp_path, _valid_source(), save_provenance=False)

    result = CandidateSandbox(artifact_service=service).run("task-sandbox", candidate, context_artifact, now=NOW)

    assert result.status == "rejected"
    assert "evidence-backed" in (result.reason or "")
    connection.close()


def _built_candidate(tmp_path: Path, source: str):
    connection, service, build_input, context_artifact = _setup(tmp_path)
    candidate = CandidateBuilder(model_client=ScriptedModel(source), artifact_service=service).build(
        "task-sandbox", build_input, candidate_id="candidate-sandbox", now=NOW
    ).candidate
    return connection, service, candidate, context_artifact


def _raw_candidate(tmp_path: Path, source: str, *, save_provenance: bool = True):
    connection, service, build_input, context_artifact = _setup(tmp_path)
    source_artifact = service.save_bytes(
        "task-sandbox",
        kind="candidate_source",
        payload=source.encode("utf-8"),
        media_type="application/x-python-code",
        source="fixture-raw-candidate",
        created_at=NOW,
    )
    candidate = CandidateFunction(
        candidate_id="candidate-raw",
        proposal_id=build_input.proposal.proposal_id,
        interface_version="signal_context_v1",
        source_artifact=source_artifact,
        source_hash=source_artifact.sha256,
    )
    repository = SignalRepository(connection)
    repository.save_proposal("task-sandbox", build_input.proposal, created_at=NOW)
    repository.save_candidate(candidate, created_at=NOW)
    if save_provenance:
        prompt_artifact = service.save_bytes(
            "task-sandbox",
            kind="model_response",
            payload=b"fixture prompt",
            media_type="text/plain",
            source="fixture-prompt",
            created_at=NOW,
        )
        repository.save_build_provenance(
            CandidateBuildProvenance(
                candidate_id=candidate.candidate_id,
                task_id="task-sandbox",
                proposal=build_input.proposal,
                prompt_artifact=prompt_artifact,
                model_id="fixture-model",
                feature_catalog=DEFAULT_FEATURE_CATALOG,
                history_artifact=build_input.history_artifact,
                build_fingerprint="a" * 64,
                created_at=NOW,
            )
        )
    return connection, service, candidate, context_artifact


def _setup(tmp_path: Path):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(
        AgentTask(
            task_id="task-sandbox",
            request=ResearchRequest(
                request_id="request-sandbox",
                question="Run a verified signal candidate safely.",
                symbols=["QQQ"],
                time_window=WINDOW,
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    history_artifact = service.save_json(
        "task-sandbox",
        kind="bars",
        payload={"bars": []},
        source="fixture-bars",
        created_at=NOW,
    )
    evidence = EvidenceService(connection, service.store).create(
        "task-sandbox",
        artifact=history_artifact,
        evidence_type="bar",
        source="fixture-bars",
        observed_at=NOW,
        evidence_id="evidence-bars",
    )
    proposal = SignalProposal(
        proposal_id="proposal-sandbox",
        hypothesis="Positive return is a research condition.",
        features=[SignalFeature(name="return_change", source="market", description="Verified return feature.")],
        logic_spec="return_change is positive",
        expected_behavior="Produces a research observation when return is positive.",
        invalidation_conditions=["insufficient bars"],
        minimum_history_bars=2,
        applicable_symbols=["QQQ"],
        evidence_refs=[evidence],
    )
    build_input = CandidateBuildInput(
        proposal=proposal,
        feature_catalog=DEFAULT_FEATURE_CATALOG,
        history_artifact=history_artifact,
        model_id="fixture-model",
    )
    context_artifact = service.save_json(
        "task-sandbox",
        kind="validation_metrics",
        payload=SignalContext(
            catalog_version=DEFAULT_FEATURE_CATALOG.version,
            symbol="QQQ",
            timestamps=(NOW - timedelta(minutes=30), NOW),
            features={"return_change": (-0.1, 0.2)},
        ).model_dump(mode="json"),
        source="fixture-context",
        created_at=NOW,
    )
    return connection, service, build_input, context_artifact

