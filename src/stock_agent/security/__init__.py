"""Security helpers for secrets, redaction, and permission checks."""

from stock_agent.security.redaction import REDACTED, redact_for_audit, redact_sensitive, redact_text
from stock_agent.security.research_policy import ResearchSafetyPolicy, SafetyDecision, SafetyRequest
from stock_agent.security.integration import SafetyIntegrationReport, build_safety_integration_report
from stock_agent.security.secrets import (
    SecretAccessBlocked,
    SecretNotFound,
    SecretValue,
    load_secret,
    load_secret_from_env,
)

__all__ = [
    "REDACTED",
    "ResearchSafetyPolicy",
    "SafetyDecision",
    "SafetyIntegrationReport",
    "SafetyRequest",
    "SecretAccessBlocked",
    "SecretNotFound",
    "SecretValue",
    "load_secret",
    "load_secret_from_env",
    "build_safety_integration_report",
    "redact_for_audit",
    "redact_sensitive",
    "redact_text",
]
