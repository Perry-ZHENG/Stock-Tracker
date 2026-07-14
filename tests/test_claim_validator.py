from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.contracts.common import TimeWindow
from stock_agent.contracts.evidence import EvidenceBundle
from stock_agent.contracts.reports import ReportClaim, ReportDraft, ReportSection
from stock_agent.contracts.tasks import AgentTask, ResearchRequest
from stock_agent.evidence.service import EvidenceService
from stock_agent.security.research_policy import ResearchSafetyPolicy
from stock_agent.storage.sqlite import initialize_database
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.validation.claims import ClaimValidator
from stock_agent.validation.evidence import EvidenceValidator
from stock_agent.validation.report import ReportValidationError, ReportValidator


NOW = datetime(2027, 1, 2, 20, 0, tzinfo=UTC)


def test_claim_validator_reproduces_numbers_and_checks_symbol_time_coverage(tmp_path: Path) -> None:
    connection, reference, artifact = _evidence(tmp_path)
    validator = _report_validator(connection, tmp_path)
    bundle = EvidenceBundle(task_id="task-claim", artifact_refs=[artifact], evidence_refs=[reference])
    valid = _claim("valid", "QQQ closed at 101.0 on 2027-01-02.", reference)
    wrong_symbol = _claim("symbol", "SPY closed at 101.0 on 2027-01-02.", reference)
    wrong_number = _claim("number", "QQQ closed at 999.0 on 2027-01-02.", reference)
    wrong_time = _claim("time", "QQQ closed at 101.0 on 2027-01-03.", reference)

    result = validator.validate(_draft(valid), bundle, now=NOW)
    symbol = validator.validate(_draft(wrong_symbol), bundle, now=NOW)
    number = validator.validate(_draft(wrong_number), bundle, now=NOW)
    time = validator.validate(_draft(wrong_time), bundle, now=NOW)

    assert result.status == "passed"
    assert symbol.claim_results[0].issues == ["claim_symbol_not_covered_by_evidence"]
    assert number.claim_results[0].issues == ["claim_number_not_reproducible_from_structured_evidence"]
    assert time.claim_results[0].issues == ["claim_time_not_covered_by_evidence"]
    connection.close()


def test_claim_validator_blocks_unsafe_language_and_requires_qualified_inference(tmp_path: Path) -> None:
    connection, reference, artifact = _evidence(tmp_path)
    validator = _report_validator(connection, tmp_path)
    bundle = EvidenceBundle(task_id="task-claim", artifact_refs=[artifact], evidence_refs=[reference])
    causal = _claim("causal", "QQQ data caused the market to rise.", reference, claim_type="inference")
    promise = _claim("promise", "This will guarantee a profit.", reference, claim_type="inference")
    trade = _claim("trade", "Automatically trade QQQ after this report.", reference, claim_type="inference")
    prediction = _claim("prediction", "QQQ will rise tomorrow.", reference, claim_type="inference")

    causal_result = validator.validate(_draft(causal), bundle, now=NOW)
    promise_result = validator.validate(_draft(promise), bundle, now=NOW)
    trade_result = validator.validate(_draft(trade), bundle, now=NOW)
    prediction_result = validator.validate(_draft(prediction), bundle, now=NOW)

    assert causal_result.status == "needs_revision"
    assert promise_result.status == "rejected"
    assert trade_result.status == "rejected"
    assert prediction_result.claim_results[0].issues == ["deterministic_price_prediction"]
    connection.close()


def test_report_validator_requires_conflict_disclosure_and_never_lets_semantic_review_clear_rules(tmp_path: Path) -> None:
    connection, reference, artifact = _evidence(tmp_path)
    reviewer = _NoopReviewer()
    validator = _report_validator(connection, tmp_path, reviewer=reviewer)
    bundle = EvidenceBundle(task_id="task-claim", artifact_refs=[artifact], evidence_refs=[reference])
    claim = _claim("claim", "QQQ closed at 999.0 on 2027-01-02.", reference)
    draft = _draft(claim)

    validation = validator.validate(draft, bundle, now=NOW, known_conflicts=["source disagreement"])

    assert validation.status == "needs_revision"
    assert "claim_number_not_reproducible_from_structured_evidence" in validation.claim_results[0].issues
    assert "undisclosed_evidence_conflicts" in validation.claim_results[0].issues
    with pytest.raises(ReportValidationError):
        validator.create_final(report_id="report-invalid", draft=draft, validation=validation, published_at=NOW)
    connection.close()


class _NoopReviewer:
    def review(self, _draft: ReportDraft, _bundle: EvidenceBundle) -> dict[str, list[str]]:
        return {}


def _report_validator(
    connection: object,
    root: Path,
    *,
    reviewer: _NoopReviewer | None = None,
) -> ReportValidator:
    service = ArtifactService(ArtifactStore(connection, root / "lake"))  # type: ignore[arg-type]
    evidence = EvidenceService(connection, service.store)  # type: ignore[arg-type]
    return ReportValidator(ClaimValidator(EvidenceValidator(evidence), ResearchSafetyPolicy(connection)), semantic_reviewer=reviewer)  # type: ignore[arg-type]


def _evidence(tmp_path: Path):
    connection = initialize_database(tmp_path / "runtime.sqlite")
    repository = TaskRepository(connection)
    repository.create_task(
        AgentTask(
            task_id="task-claim",
            request=ResearchRequest(
                request_id="request-claim",
                question="Validate a report claim.",
                symbols=["QQQ"],
                time_window=TimeWindow(
                    from_ts=NOW - timedelta(days=1),
                    to_ts=NOW,
                    timezone="America/New_York",
                ),
            ),
            created_at=NOW,
            updated_at=NOW,
        )
    )
    service = ArtifactService(ArtifactStore(connection, tmp_path / "lake"))
    artifact = service.save_json(
        "task-claim",
        kind="bars",
        payload={"bars": [{"symbol": "QQQ", "timestamp": "2027-01-02T19:30:00Z", "close": 101.0}]},
        source="fixture",
        created_at=NOW,
    )
    reference = EvidenceService(connection, service.store).create(
        "task-claim",
        artifact=artifact,
        evidence_type="bar",
        source="fixture",
        observed_at=NOW,
        evidence_id="evidence-claim",
    )
    return connection, reference, artifact


def _claim(claim_id: str, text: str, reference, *, claim_type: str = "fact") -> ReportClaim:
    return ReportClaim(
        claim_id=claim_id,
        text=text,
        claim_type=claim_type,
        confidence=0.8,
        evidence_refs=[reference],
    )


def _draft(claim: ReportClaim) -> ReportDraft:
    return ReportDraft(
        draft_id=f"draft-{claim.claim_id}",
        task_id="task-claim",
        summary="A bounded research report.",
        sections=[ReportSection(title="Findings", claim_ids=[claim.claim_id], content=claim.text)],
        claims=[claim],
        generated_at=NOW,
    )
