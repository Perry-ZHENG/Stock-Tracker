"""Production composition and step adapters for the durable V2 research flow."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from stock_agent.agents.anomaly import AnomalyAnalysisAgent, AnomalyAnalysisInput
from stock_agent.agents.macro import MacroAnalysisAgent, MacroAnalysisInput
from stock_agent.agents.registry import AgentRegistration, AgentRegistry
from stock_agent.agents.report import ReportAgent, ReportModelDraft
from stock_agent.agents.runtime import AgentRuntime, AgentRuntimeContext
from stock_agent.agents.signal_discovery import SignalDiscoveryAgent
from stock_agent.artifacts.service import ArtifactService
from stock_agent.artifacts.store import ArtifactStore
from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.contracts.analysis import AnomalyAnalysis, MacroAnalysis
from stock_agent.contracts.evidence import (
    DataEvidence,
    DataEvidenceRequest,
    EvidenceGapRequest,
    NewsEvidence,
    NewsEvidenceRequest,
)
from stock_agent.contracts.reports import FinalReport
from stock_agent.contracts.signals import SignalDiscoveryConstraints, SignalDiscoveryInput
from stock_agent.contracts.common import StrictSchema
from stock_agent.dialog.langchain_adapter import LangChainClient, build_langchain_client
from stock_agent.evidence.service import EvidenceService
from stock_agent.news.service import NewsQueryService
from stock_agent.reports.bundle import ReportBundleBuilder, ReportBundleRequest
from stock_agent.reports.service import ReportPublicationResult, ReportService
from stock_agent.security.research_policy import ResearchSafetyPolicy
from stock_agent.signals.approval import SignalApprovalService
from stock_agent.signals.registry import SignalRegistry
from stock_agent.signals.runner import ActiveSignalRunResult, ActiveSignalRunner
from stock_agent.storage.report_repository import ReportRepository
from stock_agent.storage.signal_repository import SignalRepository
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.storage.task_repository import TaskRepository
from stock_agent.validation.claims import ClaimValidator
from stock_agent.validation.evidence import EvidenceValidator
from stock_agent.validation.report import ReportValidator

from stock_agent.services.agent_service import AgentService


class ProductionV2Error(RuntimeError):
    """A persisted production step cannot be safely assembled from task data."""


class ReportPublicationStepV2(StrictSchema):
    """Runtime metadata that points to a separately persisted FinalReport."""

    report_id: str


class ReportValidationStepV2(StrictSchema):
    """Deterministic validator-step metadata without duplicating report bytes."""

    report_id: str


@dataclass(frozen=True)
class ProductionV2Components:
    """The shared composition root used by all V2 transports and workers."""

    service: AgentService
    config_context: RuntimeConfigContext

    @property
    def connection(self) -> sqlite3.Connection:
        return self.service.connection

    def close(self) -> None:
        self.connection.close()


def build_production_v2(
    root: Path,
    *,
    config_context: RuntimeConfigContext | None = None,
    connection: sqlite3.Connection | None = None,
    data_workflow: Any | None = None,
    news_workflow: Any | None = None,
    model_client: LangChainClient | None = None,
    active_signal_runner: ActiveSignalRunner | None = None,
) -> ProductionV2Components:
    """Build the real V2 path without importing legacy ReAct or broker code.

    Test callers may supply deterministic workflows and a model client. Runtime
    callers use the existing read-only provider registry and optional LangChain
    adapter, so a missing credential remains an explicit evidence gap.
    """

    resolved_root = root.resolve()
    context = config_context or load_config(resolved_root)
    active_connection = connection or initialize_runtime_database(resolved_root, context.config)
    artifacts = ArtifactService(ArtifactStore(active_connection, resolved_root / context.config.storage.parquet_root))
    resolved_data_workflow = data_workflow or _data_workflow(resolved_root, active_connection, context, artifacts)
    resolved_news_workflow = news_workflow or _news_workflow(resolved_root, active_connection, context, artifacts)
    resolved_model_client = model_client if model_client is not None else build_langchain_client(context.config.llm)
    signal_runner = active_signal_runner or ActiveSignalRunner(artifact_service=artifacts)
    report_repository = ReportRepository(active_connection)
    handler = ProductionStepHandlerV2(
        repository=TaskRepository(active_connection),
        artifact_service=artifacts,
        data_workflow=resolved_data_workflow,
        news_workflow=resolved_news_workflow,
        report_repository=report_repository,
        model_client=resolved_model_client,
        active_signal_runner=signal_runner,
    )
    registry = _registry(handler)
    runtime = AgentRuntime(
        repository=TaskRepository(active_connection),
        artifact_service=artifacts,
        registry=registry,
        model_client=resolved_model_client,
    )
    signal_registry = SignalRegistry(repository=SignalRepository(active_connection), artifact_service=artifacts)
    approval = SignalApprovalService(
        registry=signal_registry,
        admin_ids={str(identifier) for identifier in context.config.telegram.admin_user_ids},
    )
    service = AgentService(
        active_connection,
        runtime=runtime,
        approval_service=approval,
        require_final_report=True,
    )
    return ProductionV2Components(service=service, config_context=context)


class ProductionStepHandlerV2:
    """Adapt planned roles to existing V2 workflows through task-scoped artifacts."""

    def __init__(
        self,
        *,
        repository: TaskRepository,
        artifact_service: ArtifactService,
        data_workflow: Any,
        news_workflow: Any,
        report_repository: ReportRepository,
        model_client: Callable[[str], str] | None,
        active_signal_runner: ActiveSignalRunner,
    ) -> None:
        self.repository = repository
        self.artifact_service = artifact_service
        self.data_workflow = data_workflow
        self.news_workflow = news_workflow
        self.report_repository = report_repository
        self.model_client = model_client
        self.active_signal_runner = active_signal_runner

    def run(self, context: AgentRuntimeContext, typed_input: object) -> object:
        step_id = context.step.step_id
        if step_id == "step-data":
            return self._collect_data(context)
        if step_id == "step-news":
            return self._collect_news(context)
        if step_id == "step-active-signals":
            return self._run_active_signals(context)
        if step_id == "step-anomaly":
            return self._analyze_anomaly(context)
        if step_id == "step-macro":
            return self._analyze_macro(context, typed_input)
        if step_id == "step-signal-discovery":
            return self._discover_signal(context)
        if step_id.startswith("step-signal_discovery-retry-"):
            return self._discover_signal(context)
        if step_id.startswith("step-anomaly_analysis-retry-"):
            return self._analyze_anomaly(context)
        if step_id.startswith("step-macro_analysis-retry-"):
            return self._analyze_macro(context, typed_input)
        if step_id == "step-report" or step_id.startswith("step-report-retry-"):
            return self._publish_report(context)
        if step_id == "step-validator" or step_id.startswith("step-validator-retry-"):
            return self._validate_published_report(context)
        if step_id.startswith("step-gap-"):
            return self._collect_gap(context)
        raise ProductionV2Error(f"unsupported production V2 step: {step_id}")

    def _collect_data(self, context: AgentRuntimeContext) -> DataEvidence | EvidenceGapRequest:
        request = DataEvidenceRequest(
            symbols=context.task.request.symbols,
            time_window=context.task.request.time_window,
            interval="30m",
            features=["return_change", "volume_ratio", "realized_volatility", "gap", "relative_to_baseline"],
            baseline_window=2,
            # Current-data requests expose stale evidence quickly. Historical
            # research is immutable evidence, so its references do not expire.
            freshness_seconds=15 * 60 if context.task.request.constraints.require_current_data else 0,
        )
        result = self.data_workflow.collect(context.task.task_id, request, now=_context_now(context))
        if isinstance(result, DataEvidence):
            bar_evidence = next((reference for reference in result.evidence_refs if reference.evidence_type == "bar"), None)
            context.record_provider_freshness(
                provider_name=result.provider_refs[0].provider_name,
                observed_at=result.provider_refs[0].observed_at,
                valid_until=bar_evidence.valid_until if bar_evidence is not None else None,
                quality_status=result.quality.status,
            )
            return result
        return _gap(
            context.task.task_id,
            requester="report",
            evidence_type="provider" if result.code == "provider_failed" else "bar",
            reason=f"data evidence is unavailable: {result.code}: {result.message}",
        )

    def _collect_news(self, context: AgentRuntimeContext) -> NewsEvidence:
        return self.news_workflow.collect(
            context.task.task_id,
            NewsEvidenceRequest(symbols=context.task.request.symbols, time_window=context.task.request.time_window),
            now=_context_now(context),
        )

    def _run_active_signals(self, context: AgentRuntimeContext) -> ActiveSignalRunResult | EvidenceGapRequest:
        data = self._data_or_gap(context)
        if isinstance(data, EvidenceGapRequest):
            return data
        return self.active_signal_runner.run(context.task.task_id, data, now=_context_now(context))

    def _analyze_anomaly(self, context: AgentRuntimeContext) -> AnomalyAnalysis | EvidenceGapRequest:
        data = self._data_or_gap(context)
        if isinstance(data, EvidenceGapRequest):
            return data
        news = self._optional_output(context.task.task_id, "step-news", NewsEvidence)
        result = AnomalyAnalysisAgent(artifact_service=self.artifact_service).analyze(
            context.task.task_id,
            AnomalyAnalysisInput(
                data_evidence=data,
                history_artifact=data.bar_artifact,
                news_evidence=[news] if news is not None else [],
            ),
            analysis_id=f"analysis-anomaly-{context.task.task_id}",
            now=_context_now(context),
        )
        if isinstance(result, AnomalyAnalysis):
            self._save_analysis_once(context.task.task_id, result)
        return result

    def _analyze_macro(self, context: AgentRuntimeContext, typed_input: object) -> MacroAnalysis | EvidenceGapRequest:
        if not context.model_available:
            return _gap(context.task.task_id, requester="macro_analysis", evidence_type="analysis", reason="macro analysis requires a configured ModelClient")
        try:
            analysis_input = MacroAnalysisInput.model_validate(typed_input)
        except ValidationError:
            return _gap(
                context.task.task_id,
                requester="macro_analysis",
                evidence_type="mcp",
                reason="macro analysis requires verified MacroEvidenceItem input from an allowlisted source",
            )
        agent = MacroAnalysisAgent(
            model_client=lambda prompt: context.call_model(prompt, _macro_draft_schema()).model_dump_json(),
            artifact_service=self.artifact_service,
        )
        result = agent.analyze(
            context.task.task_id,
            analysis_input,
            analysis_id=f"analysis-macro-{context.task.task_id}",
            now=_context_now(context),
        )
        if isinstance(result, MacroAnalysis):
            self._save_analysis_once(context.task.task_id, result)
        return result

    def _discover_signal(self, context: AgentRuntimeContext) -> object:
        if not context.model_available:
            return _gap(context.task.task_id, requester="signal_discovery", evidence_type="analysis", reason="signal discovery requires a configured ModelClient")
        data = self._data_or_gap(context)
        if isinstance(data, EvidenceGapRequest):
            return data
        news = self._optional_output(context.task.task_id, "step-news", NewsEvidence)
        registry = SignalRegistry(repository=SignalRepository(self.repository.connection), artifact_service=self.artifact_service)
        discovery_input = SignalDiscoveryInput(
            goal=context.task.request.question,
            data_evidence=[data],
            history_artifacts=[data.bar_artifact],
            news_evidence=[news] if news is not None else [],
            existing_signals=registry.list(),
            constraints=SignalDiscoveryConstraints(allow_news_features=context.task.request.constraints.allow_news_features),
        )
        from stock_agent.contracts.signals import SignalProposal

        agent = SignalDiscoveryAgent(
            model_client=lambda prompt: context.call_model(prompt, SignalProposal).model_dump_json(),
            artifact_service=self.artifact_service,
        )
        result = agent.discover(context.task.task_id, discovery_input, now=_context_now(context))
        return result.evidence_gap or result

    def _publish_report(self, context: AgentRuntimeContext) -> ReportPublicationStepV2 | EvidenceGapRequest:
        if not context.model_available:
            return _gap(context.task.task_id, requester="report", evidence_type="analysis", reason="report generation requires a configured ModelClient")
        data = self._data_or_gap(context)
        if isinstance(data, EvidenceGapRequest):
            return data
        news = self._optional_output(context.task.task_id, "step-news", NewsEvidence)
        signals = self._optional_output(context.task.task_id, "step-active-signals", ActiveSignalRunResult)
        anomaly = self._optional_output(context.task.task_id, "step-anomaly", AnomalyAnalysis)
        macro = self._optional_output(context.task.task_id, "step-macro", MacroAnalysis)
        evidence_ids = _unique([reference.evidence_id for reference in data.evidence_refs] + ([reference.evidence_id for reference in news.evidence_refs] if news else []))
        if not evidence_ids:
            return _gap(context.task.task_id, requester="report", evidence_type="bar", reason="report requires registered data evidence")
        report_service = self._report_service(context)
        result = report_service.publish(
            ReportBundleRequest(
                task_id=context.task.task_id,
                request=context.task.request,
                evidence_ids=evidence_ids,
                signal_observations=signals.observations if signals is not None else [],
                anomaly_analysis_id=anomaly.analysis_id if anomaly is not None else None,
                macro_analysis_id=macro.analysis_id if macro is not None else None,
            ),
            limitations=_report_limitations(data, news),
            now=_context_now(context),
        )
        final_or_gap = _published_or_gap(context.task.task_id, result)
        if isinstance(final_or_gap, EvidenceGapRequest):
            return final_or_gap
        return ReportPublicationStepV2(report_id=final_or_gap.report_id)

    def _validate_published_report(self, context: AgentRuntimeContext) -> ReportValidationStepV2 | EvidenceGapRequest:
        report = self.report_repository.get_latest_final_for_task(context.task.task_id)
        if report is None:
            return _gap(context.task.task_id, requester="report", evidence_type="analysis", reason="no validated FinalReport was published by the report step")
        return ReportValidationStepV2(report_id=report.report_id)

    def _collect_gap(self, context: AgentRuntimeContext) -> object:
        """Run only the collector requested by the orchestrator's gap plan."""

        evidence_type = context.step.step_id.rsplit("-", 1)[-1]
        if evidence_type in {"bar", "provider"}:
            return self._collect_data(context)
        if evidence_type == "news":
            return self._collect_news(context)
        return _gap(
            context.task.task_id,
            requester="report",
            evidence_type=evidence_type,
            reason="the requested evidence type needs explicit allowlisted input before it can be retried",
        )

    def _data_or_gap(self, context: AgentRuntimeContext) -> DataEvidence | EvidenceGapRequest:
        output = self._latest_data_output(context.task.task_id)
        if isinstance(output, EvidenceGapRequest):
            return output
        if output is None:
            return _gap(context.task.task_id, requester="report", evidence_type="bar", reason="data evidence step has not produced task-scoped output")
        return output

    def _latest_data_output(self, task_id: str) -> DataEvidence | EvidenceGapRequest | None:
        step_ids = [
            step.step_id
            for step in self.repository.list_steps(task_id)
            if step.step_id == "step-data" or step.step_id.startswith("step-gap-")
        ]
        for step_id in reversed(step_ids):
            output = self._output(task_id, step_id, DataEvidence)
            if output is not None:
                return output
        return None

    def _optional_output(self, task_id: str, step_id: str, schema: type[BaseModel]) -> Any | None:
        output = self._output(task_id, step_id, schema)
        return None if isinstance(output, EvidenceGapRequest) else output

    def _output(self, task_id: str, step_id: str, schema: type[BaseModel]) -> Any | EvidenceGapRequest | None:
        artifact_id = self.repository.get_step_output_artifact_id(task_id, step_id)
        if artifact_id is None:
            return None
        artifact = self.repository.get_artifact(task_id, artifact_id)
        if artifact is None:
            raise ProductionV2Error(f"step output artifact is missing: {step_id}")
        payload = self.artifact_service.load_json(task_id, artifact.ref)
        try:
            return schema.model_validate(payload)
        except ValidationError:
            try:
                return EvidenceGapRequest.model_validate(payload)
            except ValidationError as exc:
                raise ProductionV2Error(f"step output has an unexpected schema: {step_id}") from exc

    def _save_analysis_once(self, task_id: str, analysis: AnomalyAnalysis | MacroAnalysis) -> None:
        if self.report_repository.get_analysis(analysis.analysis_id) is None:
            self.report_repository.save_analysis(task_id, analysis)

    def _report_service(self, context: AgentRuntimeContext) -> ReportService:
        evidence = EvidenceService(self.repository.connection, self.artifact_service.store)
        validator = ReportValidator(ClaimValidator(EvidenceValidator(evidence), ResearchSafetyPolicy(self.repository.connection)))
        report_agent = ReportAgent(
            # ReportAgent owns its two-call repair loop because it repairs both
            # JSON shape and evidence-grounding failures. Avoid nesting the
            # generic runtime schema repair inside that bounded report budget.
            model_client=lambda prompt: context.call_model(
                prompt,
                ReportModelDraft,
                repair_schema=False,
            ).model_dump_json(),
            artifact_service=self.artifact_service,
        )
        return ReportService(
            bundle_builder=ReportBundleBuilder(evidence_service=evidence, report_repository=self.report_repository),
            report_agent=report_agent,
            validator=validator,
            repository=self.report_repository,
            artifact_service=self.artifact_service,
        )


def _data_workflow(root: Path, connection: sqlite3.Connection, context: RuntimeConfigContext, artifacts: ArtifactService):
    from stock_agent.research.data_evidence import DataEvidenceWorkflow

    return DataEvidenceWorkflow(root=root, connection=connection, config_context=context, artifact_service=artifacts)


def _news_workflow(root: Path, connection: sqlite3.Connection, context: RuntimeConfigContext, artifacts: ArtifactService):
    from stock_agent.research.news_evidence import NewsEvidenceWorkflow

    query_service = NewsQueryService(connection, config=context.config.news)
    return NewsEvidenceWorkflow(root=root, connection=connection, query_service=query_service, config_context=context, artifact_service=artifacts)


def _registry(handler: ProductionStepHandlerV2) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(AgentRegistration(role="orchestrator", handler=handler, allowed_tools=frozenset({"data_evidence", "news_evidence", "query_signals"}), max_model_calls=0))
    registry.register(AgentRegistration(role="signal_discovery", handler=handler, allowed_tools=frozenset({"data_evidence", "news_evidence", "query_signals"})))
    registry.register(AgentRegistration(role="anomaly_analysis", handler=handler, allowed_tools=frozenset({"data_evidence", "news_evidence", "query_provider_compare"}), max_model_calls=0))
    registry.register(AgentRegistration(role="macro_analysis", handler=handler, allowed_tools=frozenset({"data_evidence", "news_evidence", "mcp"})))
    registry.register(
        AgentRegistration(
            role="report",
            handler=handler,
            allowed_tools=frozenset({"evidence_bundle", "claim_validator"}),
            max_model_calls=2,
        )
    )
    return registry


def _macro_draft_schema():
    from stock_agent.research.macro_evidence import MacroReasoningDraft

    return MacroReasoningDraft


def _published_or_gap(task_id: str, result: ReportPublicationResult) -> FinalReport | EvidenceGapRequest:
    if result.final_report is not None:
        return result.final_report
    if result.evidence_gap is not None:
        return result.evidence_gap
    return _gap(task_id, requester="report", evidence_type="analysis", reason=f"report publication did not produce a FinalReport: {result.status}")


def _gap(task_id: str, *, requester: str, evidence_type: str, reason: str) -> EvidenceGapRequest:
    return EvidenceGapRequest(
        task_id=task_id,
        requester=requester,  # type: ignore[arg-type]
        missing_evidence_types=[evidence_type],  # type: ignore[list-item]
        reason=reason,
    )


def _context_now(context: AgentRuntimeContext) -> datetime:
    return context.now.astimezone(UTC)


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _report_limitations(data: DataEvidence, news: NewsEvidence | None) -> list[str]:
    values = ["This research report is informational and not a trading instruction."]
    if data.quality.status != "normal":
        values.append("Market-data quality is degraded; conclusions are limited to the registered evidence.")
    if any(reference.provider_name == "synthetic_demo" for reference in data.provider_refs):
        values.append("Market bars are synthetic demo data for workflow testing and are not real market observations.")
    if news is None or not news.evidence_refs:
        values.append("No verified news evidence was available for this report.")
    return values


__all__ = [
    "ProductionStepHandlerV2",
    "ProductionV2Components",
    "ProductionV2Error",
    "ReportPublicationStepV2",
    "ReportValidationStepV2",
    "build_production_v2",
]
