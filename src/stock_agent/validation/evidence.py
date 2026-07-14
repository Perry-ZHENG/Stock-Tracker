"""Resolve report evidence references into task-scoped, structured materials."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stock_agent.contracts.evidence import EvidenceBundle, EvidenceRef
from stock_agent.evidence.service import EvidenceService, EvidenceServiceError


@dataclass(frozen=True)
class EvidenceMaterial:
    """Verified artifact payload and the values safely available for claim checks."""

    reference: EvidenceRef
    payload: object
    symbols: frozenset[str]
    timestamps: tuple[datetime, ...]
    numbers: tuple[float, ...]


class EvidenceValidator:
    """Reject references that are absent, stale, cross-task, or forged."""

    def __init__(self, evidence_service: EvidenceService) -> None:
        self.evidence_service = evidence_service

    def resolve(
        self,
        task_id: str,
        references: list[EvidenceRef],
        bundle: EvidenceBundle,
        *,
        now: datetime,
    ) -> tuple[list[EvidenceMaterial], list[str]]:
        bundle_refs = {reference.evidence_id: reference for reference in bundle.evidence_refs}
        materials: list[EvidenceMaterial] = []
        issues: list[str] = []
        for reference in references:
            bundled = bundle_refs.get(reference.evidence_id)
            if bundled != reference:
                issues.append(f"evidence_not_in_bundle:{reference.evidence_id}")
                continue
            try:
                canonical = self.evidence_service.get(task_id, reference.evidence_id, now=now)
                if canonical != reference:
                    issues.append(f"forged_or_mismatched_evidence:{reference.evidence_id}")
                    continue
                artifact = self.evidence_service._artifact_for_evidence(task_id, canonical)
                payload = self.evidence_service.artifact_store.load_json(task_id, artifact)
            except EvidenceServiceError:
                issues.append(f"evidence_unavailable:{reference.evidence_id}")
                continue
            except Exception:
                issues.append(f"artifact_unavailable:{reference.evidence_id}")
                continue
            symbols: set[str] = set()
            timestamps: list[datetime] = []
            numbers: list[float] = []
            _collect_values(payload, symbols, timestamps, numbers)
            materials.append(
                EvidenceMaterial(
                    reference=canonical,
                    payload=payload,
                    symbols=frozenset(symbols),
                    timestamps=tuple(timestamps),
                    numbers=tuple(numbers),
                )
            )
        if not materials and not issues:
            issues.append("claim_has_no_resolvable_evidence")
        return materials, issues


def _collect_values(value: object, symbols: set[str], timestamps: list[datetime], numbers: list[float]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).casefold()
            if key_text == "symbol" and isinstance(item, str):
                symbols.add(item.upper())
            elif key_text == "symbols" and isinstance(item, list):
                symbols.update(item.upper() for item in item if isinstance(item, str))
            elif key_text.endswith(("timestamp", "published_at", "observed_at", "created_at")) and isinstance(item, str):
                parsed = _parse_datetime(item)
                if parsed is not None:
                    timestamps.append(parsed)
            _collect_values(item, symbols, timestamps, numbers)
        return
    if isinstance(value, list):
        for item in value:
            _collect_values(item, symbols, timestamps, numbers)
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numbers.append(float(value))


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


__all__ = ["EvidenceMaterial", "EvidenceValidator"]
