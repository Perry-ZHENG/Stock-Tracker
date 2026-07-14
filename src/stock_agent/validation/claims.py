"""Deterministic evidence, numeric, wording, and safety checks for one claim."""

from __future__ import annotations

import re
from datetime import datetime

from stock_agent.contracts.evidence import EvidenceBundle
from stock_agent.contracts.reports import ClaimValidationResult, ReportClaim
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyRequest
from stock_agent.validation.evidence import EvidenceMaterial, EvidenceValidator

_SYMBOL_RE = re.compile(r"\b[A-Z]{1,5}\b")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER_RE = re.compile(r"(?<![\w.-])(-?\d+(?:\.\d+)?)(%)?")
_CAUSAL_RE = re.compile(r"\b(?:caused|because|led to|resulted in|therefore)\b|(?:导致|造成|因此)", re.IGNORECASE)
_CAUTIOUS_RE = re.compile(r"\b(?:may|might|could|possible|associated)\b|(?:可能|或许|相关)", re.IGNORECASE)
_DETERMINISTIC_PREDICTION_RE = re.compile(
    r"\b(?:will|must|certainly|definitely)\s+(?:rise|fall|increase|decrease|reach)\b"
    r"|(?:必然|一定|肯定).{0,12}(?:上涨|下跌|达到|涨|跌)",
    re.IGNORECASE,
)
_AUTOMATED_TRADE_RE = re.compile(
    r"\b(?:automatically|auto)\s+(?:trade|buy|sell|execute)\b|(?:自动(?:交易|买入|卖出|下单))",
    re.IGNORECASE,
)
_NON_SYMBOL_WORDS = frozenset({"A", "AN", "AND", "AS", "AT", "FOR", "FROM", "IN", "IS", "IT", "OF", "ON", "OR", "THE", "TO", "US", "WITH"})


class ClaimValidator:
    """Apply only reproducible checks; semantic reviewers may append, never clear issues."""

    def __init__(self, evidence_validator: EvidenceValidator, safety_policy: ResearchSafetyPolicy) -> None:
        self.evidence_validator = evidence_validator
        self.safety_policy = safety_policy

    def validate(
        self,
        claim: ReportClaim,
        *,
        task_id: str,
        bundle: EvidenceBundle,
        now: datetime,
        disclosure_text: str = "",
    ) -> ClaimValidationResult:
        materials, issues = self.evidence_validator.resolve(task_id, claim.evidence_refs, bundle, now=now)
        issues.extend(_coverage_issues(claim, materials))
        issues.extend(_numeric_issues(claim, materials))
        issues.extend(_wording_issues(claim, disclosure_text))
        safety = self.safety_policy.inspect(
            SafetyRequest(
                source="report_validation",
                actor_type="system",
                requested_capability="write_report",
                raw_text=claim.text,
                input_trust="trusted",
            )
        )
        if not safety.allowed:
            issues.append(f"safety:{safety.reason_code}")

        status = "passed"
        if any(issue.startswith("safety:") for issue in issues):
            status = "rejected"
        elif issues:
            status = "needs_revision"
        return ClaimValidationResult(claim_id=claim.claim_id, status=status, issues=sorted(dict.fromkeys(issues)))


def _coverage_issues(claim: ReportClaim, materials: list[EvidenceMaterial]) -> list[str]:
    issues: list[str] = []
    claim_symbols = {token for token in _SYMBOL_RE.findall(claim.text) if token not in _NON_SYMBOL_WORDS}
    material_symbols = set().union(*(material.symbols for material in materials)) if materials else set()
    if claim_symbols and material_symbols and not (claim_symbols & material_symbols):
        issues.append("claim_symbol_not_covered_by_evidence")

    claim_dates = _DATE_RE.findall(claim.text)
    material_dates = {timestamp.date().isoformat() for material in materials for timestamp in material.timestamps}
    if claim_dates and material_dates and not set(claim_dates).issubset(material_dates):
        issues.append("claim_time_not_covered_by_evidence")
    return issues


def _numeric_issues(claim: ReportClaim, materials: list[EvidenceMaterial]) -> list[str]:
    text_without_dates = _DATE_RE.sub("", claim.text)
    values = [(float(raw), percent == "%") for raw, percent in _NUMBER_RE.findall(text_without_dates)]
    if not values or claim.claim_type not in {"fact", "function_output"}:
        return []
    evidence_values = [number for material in materials for number in material.numbers]
    for value, is_percent in values:
        candidates = [value / 100] if is_percent else [value]
        if is_percent:
            candidates.append(value)
        if not any(any(_close(candidate, evidence) for evidence in evidence_values) for candidate in candidates):
            return ["claim_number_not_reproducible_from_structured_evidence"]
    return []


def _wording_issues(claim: ReportClaim, disclosure_text: str) -> list[str]:
    issues: list[str] = []
    if _CAUSAL_RE.search(claim.text) and not _CAUTIOUS_RE.search(claim.text):
        issues.append("causal_language_requires_qualified_evidence")
    if _DETERMINISTIC_PREDICTION_RE.search(claim.text):
        issues.append("deterministic_price_prediction")
    if _AUTOMATED_TRADE_RE.search(claim.text):
        issues.append("safety:automated_trading_language")
    if any(reference.trust_level == "low" for reference in claim.evidence_refs) and not _has_disclosure(disclosure_text):
        issues.append("low_trust_evidence_not_disclosed")
    if claim.claim_type == "unknown" and not _has_disclosure(disclosure_text):
        issues.append("unknown_claim_not_disclosed")
    return issues


def _close(first: float, second: float) -> bool:
    return abs(first - second) <= max(1e-6, abs(second) * 1e-4)


def _has_disclosure(text: str) -> bool:
    normalized = text.casefold()
    return any(marker in normalized for marker in ("limit", "uncertain", "conflict", "风险", "不确定", "局限", "冲突"))


__all__ = ["ClaimValidator"]
