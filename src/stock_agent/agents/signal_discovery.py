"""Evidence-gated LLM signal hypothesis generation without registry or code authority."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from pydantic import Field, ValidationError, model_validator

from stock_agent.artifacts.service import ArtifactService
from stock_agent.contracts.common import StrictSchema
from stock_agent.contracts.evidence import EvidenceGapRequest, EvidenceRef
from stock_agent.contracts.signals import SignalDiscoveryInput, SignalProposal
from stock_agent.evidence.service import EvidenceService, EvidenceServiceError
from stock_agent.security.redaction import redact_text
from stock_agent.signals.duplicate_detection import find_duplicate

SignalModelClient = Callable[[str], str]


class NoProposalResult(StrictSchema):
    reason_code: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=4_000)
    reuse_signal_id: str | None = None


class SignalDiscoveryResult(StrictSchema):
    proposal: SignalProposal | None = None
    evidence_gap: EvidenceGapRequest | None = None
    no_proposal: NoProposalResult | None = None

    @model_validator(mode="after")
    def _validate_one_outcome(self) -> "SignalDiscoveryResult":
        if sum(value is not None for value in (self.proposal, self.evidence_gap, self.no_proposal)) != 1:
            raise ValueError("SignalDiscoveryResult requires exactly one outcome")
        return self


class SignalDiscoveryAgent:
    """Generate a proposal only after all model-visible inputs are verified."""

    def __init__(
        self,
        *,
        model_client: SignalModelClient,
        artifact_service: ArtifactService,
        prompt_path: Path | None = None,
    ) -> None:
        self.model_client = model_client
        self.artifact_service = artifact_service
        self.evidence_service = EvidenceService(artifact_service.store.connection, artifact_service.store)
        self.prompt_path = prompt_path or Path(__file__).with_name("prompts") / "signal_discovery.md"

    def discover(
        self,
        task_id: str,
        discovery_input: SignalDiscoveryInput,
        *,
        now: datetime | None = None,
    ) -> SignalDiscoveryResult:
        active_now = _utc_now(now)
        preparation = self._validate_inputs(task_id, discovery_input, now=active_now)
        if isinstance(preparation, SignalDiscoveryResult):
            return preparation
        available_refs, history_bar_count, available_features = preparation
        if len(discovery_input.validation_feedback) >= discovery_input.constraints.max_revisions and discovery_input.validation_feedback:
            return _no_proposal(
                "revision_budget_exhausted",
                "validation feedback reached the configured maximum revision count",
            )

        try:
            raw = self.model_client(_render_prompt(self.prompt_path, discovery_input, available_features, history_bar_count))
            proposal = SignalProposal.model_validate_json(_extract_json(raw))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            return _no_proposal("invalid_model_output", redact_text(str(exc)) or "model output was not valid proposal JSON")
        except Exception as exc:  # pragma: no cover - external model boundary
            return _no_proposal("model_unavailable", redact_text(str(exc)) or "model is unavailable")

        validation = self._validate_proposal(
            task_id,
            proposal,
            discovery_input,
            available_refs=available_refs,
            history_bar_count=history_bar_count,
            available_features=available_features,
        )
        if validation is not None:
            return validation
        duplicate = find_duplicate(proposal, discovery_input.existing_signals)
        if duplicate is not None:
            return _no_proposal(
                "duplicate_active_signal",
                f"an active signal already covers this proposal: {duplicate.reason}",
                reuse_signal_id=duplicate.signal_id,
            )
        return SignalDiscoveryResult(proposal=proposal)

    def _validate_inputs(
        self,
        task_id: str,
        discovery_input: SignalDiscoveryInput,
        *,
        now: datetime,
    ) -> tuple[dict[str, EvidenceRef], int, set[str]] | SignalDiscoveryResult:
        data_artifacts = {evidence.bar_artifact.artifact_id for evidence in discovery_input.data_evidence}
        history_ids = {artifact.artifact_id for artifact in discovery_input.history_artifacts}
        if not data_artifacts.issubset(history_ids):
            return _gap(task_id, ["bar"], "DataEvidence bar artifacts must be present in history_artifacts")
        for artifact in discovery_input.history_artifacts:
            try:
                self.artifact_service.open_bytes(task_id, artifact)
            except Exception:
                return _gap(task_id, ["bar"], "history artifact is missing, expired, or fails hash verification")

        available_refs: dict[str, EvidenceRef] = {}
        available_features: set[str] = set()
        history_bar_count = 0
        for data_evidence in discovery_input.data_evidence:
            if data_evidence.quality.status == "unavailable":
                return _gap(task_id, ["bar"], "DataEvidence is unavailable")
            if any(flag.startswith(("baseline_insufficient", "insufficient_bars", "no_usable_bars")) for flag in data_evidence.quality.flags):
                return _gap(task_id, ["bar"], "DataEvidence does not provide a sufficient historical baseline")
            history_bar_count += _bar_count(self.artifact_service.load_json(task_id, data_evidence.bar_artifact))
            for feature in data_evidence.features:
                available_features.add(feature.name.rsplit(".", 1)[-1])
            validated = self._validate_evidence_refs(task_id, data_evidence.evidence_refs, now=now)
            if validated is None:
                return _gap(task_id, ["bar"], "DataEvidence references are unavailable")
            available_refs.update(validated)
        for news_evidence in discovery_input.news_evidence:
            validated = self._validate_evidence_refs(task_id, news_evidence.evidence_refs, now=now)
            if validated is None:
                return _gap(task_id, ["news"], "NewsEvidence references are unavailable")
            available_refs.update(validated)
        if history_bar_count < 2:
            return _gap(task_id, ["bar"], "history artifacts do not contain enough bars")
        return available_refs, history_bar_count, available_features

    def _validate_evidence_refs(
        self,
        task_id: str,
        references: list[EvidenceRef],
        *,
        now: datetime,
    ) -> dict[str, EvidenceRef] | None:
        try:
            canonical = [self.evidence_service.get(task_id, reference.evidence_id, now=now) for reference in references]
        except EvidenceServiceError:
            return None
        if canonical != references:
            return None
        return {reference.evidence_id: reference for reference in canonical}

    def _validate_proposal(
        self,
        task_id: str,
        proposal: SignalProposal,
        discovery_input: SignalDiscoveryInput,
        *,
        available_refs: dict[str, EvidenceRef],
        history_bar_count: int,
        available_features: set[str],
    ) -> SignalDiscoveryResult | None:
        try:
            proposal.validate_discovery_input(discovery_input)
        except ValueError as exc:
            return _no_proposal("proposal_constraint_failed", str(exc))
        proposal_refs = {reference.evidence_id for reference in proposal.evidence_refs}
        if not proposal_refs.issubset(available_refs) or any(
            available_refs.get(reference.evidence_id) != reference for reference in proposal.evidence_refs
        ):
            return _no_proposal("unknown_evidence_reference", "proposal referenced evidence that is not part of its input")
        if proposal.minimum_history_bars > history_bar_count:
            return _gap(task_id, ["bar"], "proposal requires more history bars than verified artifacts contain")
        market_features = {feature.name.rsplit(".", 1)[-1] for feature in proposal.features if feature.source == "market"}
        if not market_features.issubset(available_features):
            return _gap(task_id, ["bar"], "proposal requires market features unavailable in DataEvidence")
        news_reference_ids = {
            reference.evidence_id
            for evidence in discovery_input.news_evidence
            for reference in evidence.evidence_refs
        }
        if proposal.requires_news_evidence() and not (proposal_refs & news_reference_ids):
            return _no_proposal(
                "news_feature_without_news_reference",
                "news-driven proposal must cite a verified NewsEvidence reference",
            )
        text = "\n".join([proposal.logic_spec, proposal.expected_behavior, *proposal.invalidation_conditions]).casefold()
        if any(token in text for token in ("future", "lookahead", "next bar", "未来", "前视")):
            return _no_proposal("future_feature_forbidden", "proposal uses a future or look-ahead feature")
        return None


def _gap(task_id: str, evidence_types: list[str], reason: str) -> SignalDiscoveryResult:
    return SignalDiscoveryResult(
        evidence_gap=EvidenceGapRequest(
            task_id=task_id,
            requester="signal_discovery",
            missing_evidence_types=evidence_types,
            reason=reason,
        )
    )


def _no_proposal(reason_code: str, message: str, *, reuse_signal_id: str | None = None) -> SignalDiscoveryResult:
    return SignalDiscoveryResult(no_proposal=NoProposalResult(reason_code=reason_code, message=message, reuse_signal_id=reuse_signal_id))


def _bar_count(payload: object) -> int:
    if isinstance(payload, dict) and isinstance(payload.get("bars"), list):
        return len(payload["bars"])
    return 0


def _render_prompt(path: Path, discovery_input: SignalDiscoveryInput, features: set[str], history_bar_count: int) -> str:
    template = path.read_text(encoding="utf-8")
    return "\n\n".join(
        [
            template.strip(),
            "Available market features: " + ", ".join(sorted(features)),
            f"Verified history bars: {history_bar_count}",
            "Input evidence IDs: "
            + ", ".join(
                sorted(
                    [
                        reference.evidence_id
                        for evidence in discovery_input.data_evidence
                        for reference in evidence.evidence_refs
                    ]
                    + [
                        reference.evidence_id
                        for evidence in discovery_input.news_evidence
                        for reference in evidence.evidence_refs
                    ]
                )
            ),
            "SignalProposal JSON schema: " + json.dumps(SignalProposal.model_json_schema(), ensure_ascii=False, sort_keys=True),
        ]
    )


def _extract_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        text = text.split("\n", 1)[1].rsplit("\n", 1)[0]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("model output must be a JSON object")
    return json.dumps(parsed, ensure_ascii=False)


def _utc_now(value: datetime | None) -> datetime:
    active_now = value or datetime.now(UTC)
    if active_now.tzinfo is None:
        raise ValueError("signal discovery time must be timezone-aware")
    return active_now.astimezone(UTC)


__all__ = ["NoProposalResult", "SignalDiscoveryAgent", "SignalDiscoveryResult", "SignalModelClient"]
