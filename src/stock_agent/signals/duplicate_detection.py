"""Deterministic duplicate checks for proposed research signals."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from stock_agent.contracts.signals import ExistingSignal, SignalProposal


@dataclass(frozen=True)
class DuplicateSignalMatch:
    signal_id: str
    reason: str


def proposal_fingerprint(proposal: SignalProposal) -> str:
    """Hash only semantic signal inputs, never an LLM-generated identifier."""

    features = ",".join(sorted(f"{feature.source}:{feature.name.casefold()}" for feature in proposal.features))
    logic = _normalize(proposal.logic_spec)
    return hashlib.sha256(f"{features}|{logic}".encode("utf-8")).hexdigest()


def find_duplicate(proposal: SignalProposal, existing_signals: list[ExistingSignal]) -> DuplicateSignalMatch | None:
    fingerprint = proposal_fingerprint(proposal)
    name = _normalize(proposal.hypothesis)
    for signal in existing_signals:
        if signal.status != "active":
            continue
        if signal.feature_fingerprint == fingerprint:
            return DuplicateSignalMatch(signal.signal_id, "matching_feature_fingerprint")
        if _normalize(signal.name) == name:
            return DuplicateSignalMatch(signal.signal_id, "matching_normalized_name")
    return None


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", value.casefold())).strip()


__all__ = ["DuplicateSignalMatch", "find_duplicate", "proposal_fingerprint"]
