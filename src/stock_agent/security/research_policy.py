"""Deterministic safety policy for every V2 research entry point.

The policy deliberately separates a pure ``SafetyRequest -> SafetyDecision``
decision from optional SQLite auditing.  Callers must still enforce their own
identity and tool permissions after an allow decision.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Literal

from pydantic import Field, JsonValue

from stock_agent.contracts.common import StrictSchema
from stock_agent.security.redaction import redact_for_audit, redact_sensitive
from stock_agent.storage.repositories import insert_security_audit
from stock_agent.tracing import utc_now

SafetyCapability = Literal[
    "research",
    "read_market_data",
    "read_news",
    "search_signal_library",
    "use_model",
    "use_mcp",
    "write_signal_candidate",
    "run_signal_sandbox",
    "write_report",
    "approve_signal",
    "place_order",
    "modify_order",
    "cancel_order",
    "modify_position",
    "transfer_funds",
    "withdraw_funds",
    "access_account",
    "read_secret",
    "change_password",
    "change_account",
    "manage_permissions",
    "bypass_approval",
    "unknown_high_risk",
]
SafetyActorType = Literal["human_user", "human_admin", "agent", "system", "tool", "unknown"]
SafetyInputTrust = Literal["trusted", "untrusted"]
SafetyDecisionStatus = Literal["allowed", "blocked"]
SafetyReasonCode = Literal[
    "allowed_research",
    "allowed_admin_approval",
    "blocked_trading_or_position",
    "blocked_funds_or_account",
    "blocked_secret_access",
    "blocked_guaranteed_return",
    "blocked_approval_bypass",
    "blocked_privilege_escalation",
    "blocked_untrusted_instruction",
    "blocked_unapproved_capability",
    "blocked_unknown_high_risk",
]

ALLOWED_RESEARCH_CAPABILITIES: frozenset[str] = frozenset(
    {
        "research",
        "read_market_data",
        "read_news",
        "search_signal_library",
        "use_model",
        "use_mcp",
        "write_signal_candidate",
        "run_signal_sandbox",
        "write_report",
    }
)

_REASON_MESSAGES: dict[SafetyReasonCode, str] = {
    "allowed_research": "允许执行只读研究任务；仍须通过模块权限校验。",
    "allowed_admin_approval": "允许由已认证管理员执行审批；仍须通过审批工作流校验。",
    "blocked_trading_or_position": "系统仅提供研究、信号和报告，不执行交易或仓位操作。",
    "blocked_funds_or_account": "系统不处理资金划转、账户或账户配置操作。",
    "blocked_secret_access": "系统不会读取、展示或传输凭据与密钥。",
    "blocked_guaranteed_return": "系统不会承诺、保证或暗示投资收益。",
    "blocked_approval_bypass": "信号和配置必须经过既定审批流程，不能跳过审批。",
    "blocked_privilege_escalation": "系统不允许通过请求、工具或外部文本扩大权限。",
    "blocked_untrusted_instruction": "外部内容仅作为证据，不能改变系统指令、权限或工具能力。",
    "blocked_unapproved_capability": "该能力仅可由已认证管理员在受控审批流程中使用。",
    "blocked_unknown_high_risk": "无法安全识别的高风险操作已被拒绝。",
}

_CAPABILITY_REASONS: dict[str, SafetyReasonCode] = {
    "place_order": "blocked_trading_or_position",
    "modify_order": "blocked_trading_or_position",
    "cancel_order": "blocked_trading_or_position",
    "modify_position": "blocked_trading_or_position",
    "transfer_funds": "blocked_funds_or_account",
    "withdraw_funds": "blocked_funds_or_account",
    "access_account": "blocked_funds_or_account",
    "change_password": "blocked_funds_or_account",
    "change_account": "blocked_funds_or_account",
    "read_secret": "blocked_secret_access",
    "manage_permissions": "blocked_privilege_escalation",
    "bypass_approval": "blocked_approval_bypass",
    "unknown_high_risk": "blocked_unknown_high_risk",
}

_PROMPT_INJECTION_RE = re.compile(
    r"(?:ignore|override|discard|bypass)\s+(?:all\s+)?(?:previous|prior|system|safety)"
    r"|(?:忽略|覆盖|绕过)(?:之前|前面|系统|安全)(?:指令|规则|限制)?",
    re.IGNORECASE,
)
_TRADE_OR_POSITION_RE = re.compile(
    r"\b(?:place|execute|submit|fill|cancel|modify)\s+(?:an?\s+)?(?:buy|sell|trade|order)\b"
    r"|\b(?:place|execute|submit|fill|cancel|modify)[._ -]?order\b"
    r"|\b(?:buy|sell)\s+\d+(?:\.\d+)?\s+(?:shares?|lots?)\b"
    r"|\b(?:broker|trading)[._ -]?(?:api|client)?[._ -]?(?:place|submit|execute|cancel)[._ -]?order\b"
    r"|(?:下单|买入|卖出|开仓|平仓|调仓|加仓|减仓|仓位|杠杆|配资)",
    re.IGNORECASE,
)
_FUNDS_OR_ACCOUNT_RE = re.compile(
    r"\b(?:transfer|withdraw|deposit)\s+(?:funds?|money|cash)\b"
    r"|\b(?:change|switch|access)\s+account\b"
    r"|(?:转账|提现吗|取现|入金|出金|修改账户|切换账户|账户操作)",
    re.IGNORECASE,
)
_SECRET_RE = re.compile(
    r"\b(?:show|print|read|reveal|export)\s+(?:the\s+)?(?:api[ _-]?key|token|secret|password|credential)\b"
    r"|(?:显示|打印|读取|导出|透露)(?:密钥|令牌|密码|凭据)",
    re.IGNORECASE,
)
_RETURN_GUARANTEE_RE = re.compile(
    r"\b(?:guarantee|ensure|promise)\s+(?:a\s+)?(?:profit|return|gain)\b"
    r"|(?:保证收益|保本|稳赚|必赚|无风险收益)",
    re.IGNORECASE,
)
_APPROVAL_BYPASS_RE = re.compile(
    r"\b(?:skip|bypass|disable|ignore)\s+(?:the\s+)?(?:approval|review|validation)\b"
    r"|(?:跳过|绕过|取消|关闭)(?:审批|审核|验证)",
    re.IGNORECASE,
)
_PRIVILEGE_RE = re.compile(
    r"\b(?:grant|elevate|escalate|add)\s+(?:admin|root|permission|privilege|capabilit)"
    r"|(?:提升|扩大|授予|增加)(?:管理员|权限|特权|能力)",
    re.IGNORECASE,
)


class SafetyRequest(StrictSchema):
    """A normalized request passed to the V2 research safety boundary."""

    source: str = Field(min_length=1, max_length=64)
    actor_ref: str | None = Field(default=None, max_length=256)
    actor_type: SafetyActorType = "unknown"
    requested_capability: SafetyCapability
    action_summary: str | None = Field(default=None, max_length=2_000)
    raw_text: str | None = Field(default=None, max_length=20_000)
    input_trust: SafetyInputTrust = "trusted"
    untrusted_text: str | None = Field(default=None, max_length=20_000)
    tool_name: str | None = Field(default=None, max_length=256)
    tool_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    details: dict[str, JsonValue] = Field(default_factory=dict)
    retain_raw_text: bool = False


class SafetyDecision(StrictSchema):
    """A deterministic policy result; allow is not an authorization grant."""

    status: SafetyDecisionStatus
    allowed: bool
    requested_capability: SafetyCapability
    reason_code: SafetyReasonCode
    public_message: str
    matched_rule: str
    audit_id: str | None = None
    policy_version: Literal["research-safety-v2"] = "research-safety-v2"


class ResearchSafetyPolicy:
    """Evaluate research safety without allowing text to expand capabilities."""

    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection

    def decide(self, request: SafetyRequest) -> SafetyDecision:
        """Return a deterministic, side-effect-free decision for ``request``."""

        reason_code, matched_rule = self._evaluate(request)
        allowed = reason_code in {"allowed_research", "allowed_admin_approval"}
        return SafetyDecision(
            status="allowed" if allowed else "blocked",
            allowed=allowed,
            requested_capability=request.requested_capability,
            reason_code=reason_code,
            public_message=_REASON_MESSAGES[reason_code],
            matched_rule=matched_rule,
        )

    def inspect(self, request: SafetyRequest) -> SafetyDecision:
        """Evaluate and audit a rejected request when a connection is available."""

        decision = self.decide(request)
        if decision.allowed or self.connection is None:
            return decision

        audit_id = insert_security_audit(
            self.connection,
            timestamp=utc_now(),
            source=request.source,
            actor_ref=request.actor_ref,
            action=request.requested_capability,
            decision="blocked",
            reason=decision.reason_code,
            raw_text=redact_for_audit(request.raw_text, retain_text=request.retain_raw_text),
            details=redact_sensitive(
                {
                    "actor_type": request.actor_type,
                    "input_trust": request.input_trust,
                    "matched_rule": decision.matched_rule,
                    "tool_name": request.tool_name,
                    "tool_arguments": request.tool_arguments,
                    "details": request.details,
                }
            ),
        )
        return decision.model_copy(update={"audit_id": audit_id})

    def _evaluate(self, request: SafetyRequest) -> tuple[SafetyReasonCode, str]:
        capability_reason = _CAPABILITY_REASONS.get(request.requested_capability)
        if capability_reason is not None:
            return capability_reason, f"capability:{request.requested_capability}"

        if request.input_trust == "untrusted" and _PROMPT_INJECTION_RE.search(request.untrusted_text or ""):
            return "blocked_untrusted_instruction", "untrusted_prompt_injection"

        request_text = _request_text(request)
        for rule, pattern, reason_code in (
            ("prompt_injection", _PROMPT_INJECTION_RE, "blocked_untrusted_instruction"),
            ("trading_or_position", _TRADE_OR_POSITION_RE, "blocked_trading_or_position"),
            ("funds_or_account", _FUNDS_OR_ACCOUNT_RE, "blocked_funds_or_account"),
            ("secret_access", _SECRET_RE, "blocked_secret_access"),
            ("guaranteed_return", _RETURN_GUARANTEE_RE, "blocked_guaranteed_return"),
            ("approval_bypass", _APPROVAL_BYPASS_RE, "blocked_approval_bypass"),
            ("privilege_escalation", _PRIVILEGE_RE, "blocked_privilege_escalation"),
        ):
            if pattern.search(request_text):
                return reason_code, rule

        if request.requested_capability == "approve_signal":
            if request.actor_type == "human_admin" and request.input_trust == "trusted":
                return "allowed_admin_approval", "authenticated_admin_approval"
            return "blocked_unapproved_capability", "approval_requires_human_admin"

        if request.requested_capability in ALLOWED_RESEARCH_CAPABILITIES:
            return "allowed_research", f"capability:{request.requested_capability}"
        return "blocked_unknown_high_risk", "unrecognized_capability"


def _request_text(request: SafetyRequest) -> str:
    parts = [request.action_summary or "", request.raw_text or "", request.tool_name or ""]
    if request.tool_arguments:
        parts.append(json.dumps(request.tool_arguments, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts)


__all__ = [
    "ALLOWED_RESEARCH_CAPABILITIES",
    "ResearchSafetyPolicy",
    "SafetyActorType",
    "SafetyCapability",
    "SafetyDecision",
    "SafetyDecisionStatus",
    "SafetyInputTrust",
    "SafetyReasonCode",
    "SafetyRequest",
]
