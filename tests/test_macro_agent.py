from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_agent.agents.macro import MacroAnalysisAgent, MacroAnalysisInput
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.analysis import MacroAnalysis
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import DataEvidence, DataQuality, EvidenceGapRequest, ProviderReference
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.research.macro_evidence import MacroEvidenceItem
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository


NOW = datetime(2027, 4, 4, 20, 0, tzinfo=UTC)
WINDOW = TimeWindow(from_ts=NOW - timedelta(days=10), to_ts=NOW, timezone="America/New_York")


class ScriptedModel:
    def __init__(self, output: str | Exception) -> None:
        self.output = output
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


def test_macro_agent_generates_grounded_paths_and_multiple_scenarios(tmp_path: Path) -> None:
    connection, service, analysis_input, refs = _input(tmp_path, include_cross_asset=True)
    model = ScriptedModel(_draft(refs["rate"], refs["index"], refs["market"]))

    result = MacroAnalysisAgent(model_client=model, artifact_service=service).analyze(
        "task-macro", analysis_input, analysis_id="macro-1", now=NOW
    )

    assert isinstance(result, MacroAnalysis)
    assert {scenario.name for scenario in result.alternative_scenarios} == {"base", "alternative"}
    assert result.transmission_paths[0].assumptions
    assert result.transmission_paths[0].uncertainties
    assert result.transmission_paths[0].falsification_conditions
    assert result.confidence == 0.55
    assert "untrusted data" in model.prompts[0]
    assert all("$" not in scenario.description for scenario in result.alternative_scenarios)
    connection.close()


def test_macro_agent_exposes_conflicting_indicators_and_lowers_confidence(tmp_path: Path) -> None:
    connection, service, analysis_input, refs = _input(tmp_path, conflicting=True)
    model = ScriptedModel(_draft(refs["rate"], refs["inflation"], refs["market"]))

    result = MacroAnalysisAgent(model_client=model, artifact_service=service).analyze(
        "task-macro", analysis_input, analysis_id="macro-2", now=NOW
    )

    assert isinstance(result, MacroAnalysis)
    assert result.confidence == 0.3
    assert "\"has_conflicting_macro_indicators\": true" in model.prompts[0]
    connection.close()


def test_macro_agent_requests_cross_asset_evidence_when_required(tmp_path: Path) -> None:
    connection, service, analysis_input, refs = _input(tmp_path)

    result = MacroAnalysisAgent(model_client=ScriptedModel(_draft(refs["rate"], refs["rate"], refs["market"])), artifact_service=service).analyze(
        "task-macro",
        analysis_input.model_copy(update={"require_cross_asset_evidence": True}),
        analysis_id="macro-3",
        now=NOW,
    )

    assert isinstance(result, EvidenceGapRequest)
    assert result.missing_evidence_types == ["mcp"]
    assert "cross-asset" in result.reason
    connection.close()


def test_macro_agent_rejects_expired_policy_evidence(tmp_path: Path) -> None:
    connection, service, analysis_input, refs = _input(tmp_path, expired_rate=True)

    result = MacroAnalysisAgent(model_client=ScriptedModel(_draft(refs["rate"], refs["rate"], refs["market"])), artifact_service=service).analyze(
        "task-macro", analysis_input, analysis_id="macro-4", now=NOW
    )

    assert isinstance(result, EvidenceGapRequest)
    assert "expired" in result.reason
    connection.close()


def test_macro_agent_blocks_price_points_and_handles_unavailable_upstream_reasoning(tmp_path: Path) -> None:
    connection, service, analysis_input, refs = _input(tmp_path)
    unsafe = _draft(refs["rate"], refs["rate"], refs["market"], alternative="The share price will reach $999.")
    unsafe_result = MacroAnalysisAgent(model_client=ScriptedModel(unsafe), artifact_service=service).analyze(
        "task-macro", analysis_input, analysis_id="macro-5", now=NOW
    )
    unavailable_result = MacroAnalysisAgent(
        model_client=ScriptedModel(RuntimeError("fake MCP source unavailable")), artifact_service=service
    ).analyze("task-macro", analysis_input, analysis_id="macro-6", now=NOW)

    assert isinstance(unsafe_result, EvidenceGapRequest)
    assert "deterministic price point" in unsafe_result.reason
    assert isinstance(unavailable_result, EvidenceGapRequest)
    assert "unavailable" in unavailable_result.reason
    connection.close()


def _input(
    tmp_path: Path,
    *,
    include_cross_asset: bool = False,
    conflicting: bool = False,
    expired_rate: bool = False,
) -> tuple[object, ArtifactService, MacroAnalysisInput, dict[str, str]]:
    connection = initialize_database(tmp_path / "runtime.sqlite")
    TaskRepository(connection).create_task(
        AgentTask(
            task_id="task-macro",
            request=ResearchRequest(
                request_id="request-macro",
                question="Assess macro transmission without providing a trading instruction.",
                symbols=["QQQ"],
                time_window=WINDOW,
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    evidence_service = EvidenceService(connection, service.store)
    market_artifact = service.save_json(
        "task-macro",
        kind="bars",
        payload={"bars": []},
        source="fixture-market",
        created_at=NOW,
    )
    market_ref = evidence_service.create(
        "task-macro",
        artifact=market_artifact,
        evidence_type="bar",
        source="fixture-market",
        observed_at=NOW,
        evidence_id="evidence-market",
    )
    market = DataEvidence(
        request={"symbols": ["QQQ"], "time_window": WINDOW},
        bar_artifact=market_artifact,
        summary="QQQ market evidence is available for the requested window.",
        quality=DataQuality(),
        provider_refs=[ProviderReference(provider_name="fixture", request_id="market-1", observed_at=NOW)],
        evidence_refs=[market_ref],
    )
    rate, rate_ref = _macro_item(
        service,
        evidence_service,
        event_id="event-rate",
        kind="rate",
        stance="restrictive",
        description="The policy rate remained restrictive in the observed period.",
        evidence_id="evidence-rate",
        expired=expired_rate,
    )
    evidence_items = [rate]
    refs = {"rate": rate_ref.evidence_id, "market": market_ref.evidence_id}
    if include_cross_asset:
        index, index_ref = _macro_item(
            service,
            evidence_service,
            event_id="event-index",
            kind="index",
            stance="restrictive",
            description="The broad index was weak during the observed period.",
            evidence_id="evidence-index",
        )
        evidence_items.append(index)
        refs["index"] = index_ref.evidence_id
    if conflicting:
        inflation, inflation_ref = _macro_item(
            service,
            evidence_service,
            event_id="event-inflation",
            kind="inflation",
            stance="supportive",
            description="Inflation data was more favorable than expected in the observed period.",
            evidence_id="evidence-inflation",
        )
        evidence_items.append(inflation)
        refs["inflation"] = inflation_ref.evidence_id
    return (
        connection,
        service,
        MacroAnalysisInput(
            macro_evidence=evidence_items,
            market_evidence=[market],
            target_symbol="QQQ",
            target_industry="technology",
            time_window=WINDOW,
        ),
        refs,
    )


def _macro_item(
    service: ArtifactService,
    evidence_service: EvidenceService,
    *,
    event_id: str,
    kind: str,
    stance: str,
    description: str,
    evidence_id: str,
    expired: bool = False,
) -> tuple[MacroEvidenceItem, object]:
    source = f"mcp:fixture-{event_id}"
    artifact = service.save_json(
        "task-macro",
        kind="model_response",
        payload={"event_id": event_id, "description": description},
        source=source,
        created_at=NOW - timedelta(days=2),
    )
    reference = evidence_service.create(
        "task-macro",
        artifact=artifact,
        evidence_type="mcp",
        source=source,
        observed_at=NOW - timedelta(days=2),
        valid_until=NOW - timedelta(days=1) if expired else None,
        evidence_id=evidence_id,
    )
    return (
        MacroEvidenceItem(
            event_id=event_id,
            kind=kind,
            stance=stance,
            description=description,
            occurred_at=NOW - timedelta(days=2),
            evidence_refs=[reference],
        ),
        reference,
    )


def _draft(rate: str, second: str, market: str, *, alternative: str = "A different path could dominate if conditions diverge.") -> str:
    return json.dumps(
        {
            "paths": [
                {
                    "event_id": "event-rate",
                    "intermediate_variable": "discount-rate sensitivity",
                    "affected_scope": "technology",
                    "expected_window": "medium term",
                    "evidence_ids": [rate, market],
                    "assumptions": ["Financing conditions remain relevant to valuation decisions."],
                    "uncertainties": ["Company-specific earnings may dominate the macro channel."],
                    "falsification_conditions": ["Market and sector evidence remain resilient despite the policy channel."],
                    "confidence": 0.4,
                }
            ],
            "scenarios": [
                {
                    "name": "base",
                    "description": "The observed policy channel remains one conditional input among other drivers.",
                    "evidence_ids": [rate, market],
                },
                {
                    "name": "alternative",
                    "description": alternative,
                    "evidence_ids": [second],
                },
            ],
        }
    )
