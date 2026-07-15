"""Aggregate deterministic claim checks and expose the only V2 finalization path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from stock_agent.contracts.evidence import EvidenceBundle
from stock_agent.contracts.reports import FinalReport, ReportClaim, ReportDraft, ReportValidationResult
from stock_agent.observability import AgentTrace, AgentTraceRecorder
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.validation.claims import ClaimValidator


class SemanticReviewer(Protocol):
    """An optional model review may report concerns but cannot approve or edit facts."""

    def review(self, draft: ReportDraft, bundle: EvidenceBundle) -> dict[str, list[str]]: ...


class ReportValidationError(ValueError):
    """Raised when a caller tries to finalise an unvalidated report draft."""


class ReportValidator:
    def __init__(
        self,
        claim_validator: ClaimValidator,
        *,
        semantic_reviewer: SemanticReviewer | None = None,
        safety_policy: ResearchSafetyPolicy | None = None,
    ) -> None:
        self.claim_validator = claim_validator
        self.semantic_reviewer = semantic_reviewer
        self.safety_policy = safety_policy or claim_validator.safety_policy

    def validate(
        self,
        draft: ReportDraft,
        bundle: EvidenceBundle,
        *,
        now: datetime,
        known_conflicts: list[str] | None = None,
    ) -> ReportValidationResult:
        if bundle.task_id != draft.task_id:
            return ReportValidationResult(
                status="needs_revision",
                claim_results=[
                    self.claim_validator.validate(
                        claim,
                        task_id=draft.task_id,
                        bundle=EvidenceBundle(task_id=draft.task_id),
                        now=now,
                    )
                    for claim in draft.claims
                ],
                missing_evidence=["bundle_task_mismatch"],
            )
        disclosure_text = "\n".join([draft.summary, *draft.limitations, *(section.content for section in draft.sections)])
        results = [
            self.claim_validator.validate(
                claim,
                task_id=draft.task_id,
                bundle=bundle,
                now=now,
                disclosure_text=disclosure_text,
            )
            for claim in draft.claims
        ]
        unresolved_conflicts = known_conflicts or []
        if unresolved_conflicts and not _has_conflict_disclosure(disclosure_text):
            results = _append_issue(results, draft.claims[0].claim_id, "undisclosed_evidence_conflicts")
        if self.semantic_reviewer is not None:
            results = _append_semantic_issues(results, self.semantic_reviewer.review(draft, bundle))

        statuses = {result.status for result in results}
        status = "rejected" if "rejected" in statuses else "needs_revision" if "needs_revision" in statuses else "passed"
        missing = [
            result.claim_id
            for result in results
            if any("evidence" in issue or "artifact" in issue for issue in result.issues)
        ]
        return ReportValidationResult(status=status, claim_results=results, missing_evidence=sorted(set(missing)))

    def create_final(
        self,
        *,
        report_id: str,
        draft: ReportDraft,
        validation: ReportValidationResult,
        published_at: datetime | None = None,
    ) -> FinalReport:
        if validation.status != "passed":
            raise ReportValidationError("a report with unresolved validation issues cannot be finalised")
        active_published = published_at or datetime.now(UTC)
        if active_published.tzinfo is None:
            raise ReportValidationError("published_at must be timezone-aware")
        safety = self.safety_policy.inspect(
            SafetyRequest(
                source="report_validator",
                actor_type="system",
                requested_capability="write_report",
                raw_text="\n".join(
                    [draft.summary, *draft.limitations, *(section.content for section in draft.sections)]
                ),
            )
        )
        if not safety.allowed:
            self._record_safety_block(draft, safety.reason_code, safety.audit_id, active_published)
            raise ReportValidationError(f"report finalisation is blocked: {safety.reason_code}")
        return FinalReport(
            report_id=report_id,
            draft=draft,
            validation=validation,
            published_at=active_published,
        )

    def _record_safety_block(self, draft: ReportDraft, reason_code: str, audit_id: str | None, now: datetime) -> None:
        if self.safety_policy.connection is None:
            return
        AgentTraceRecorder(self.safety_policy.connection).record(
            AgentTrace(
                trace_id=f"trace-safety-{draft.task_id}-{uuid4().hex}",
                task_id=draft.task_id,
                component="report",
                status="failed",
                error_category="safety",
                input_ref={"boundary": "report_validator", "draft_id": draft.draft_id},
                output_ref={"audit_id": audit_id, "reason_code": reason_code},
                error_message=reason_code,
                created_at=now,
            )
        )


def _append_semantic_issues(results, reviewer_issues: dict[str, list[str]]):
    output = results
    for claim_id, issues in reviewer_issues.items():
        for issue in issues:
            output = _append_issue(output, claim_id, f"semantic_review:{issue}")
    return output


def _append_issue(results, claim_id: str, issue: str):
    output = []
    for result in results:
        if result.claim_id != claim_id:
            output.append(result)
            continue
        status = "rejected" if result.status == "rejected" else "needs_revision"
        output.append(result.model_copy(update={"status": status, "issues": [*result.issues, issue]}))
    return output


def _has_conflict_disclosure(text: str) -> bool:
    normalized = text.casefold()
    return "conflict" in normalized or "冲突" in normalized


__all__ = ["ReportValidationError", "ReportValidator", "SemanticReviewer"]
