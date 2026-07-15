"""FastAPI workbench for the V2 evidence-first research Agent."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.contracts.reports import FinalReport
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.observability import AgentTraceRecorder, BudgetLedger
from stock_agent.reports.renderers import render_report
from stock_agent.services.production_v2 import ProductionV2Components, build_production_v2
from stock_agent.web.agent_service import WebAgentError, WebAgentService

if TYPE_CHECKING:
    from stock_agent.services.agent_service import AgentService


WEB_ROOT = Path(__file__).resolve().parent


class ResearchSubmitRequest(BaseModel):
    request: ResearchRequest


class ResearchInputRequest(BaseModel):
    step_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, object] = Field(default_factory=dict)


def create_app(
    root: Path,
    *,
    config_context: RuntimeConfigContext | None = None,
    v2_agent_service: "AgentService | None" = None,
) -> FastAPI:
    resolved_root = root.resolve()
    context = config_context or load_config(resolved_root)
    owned_components: ProductionV2Components | None = None
    if v2_agent_service is None:
        owned_components = build_production_v2(resolved_root, config_context=context)
        v2_agent_service = owned_components.service

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            if owned_components is not None:
                owned_components.close()

    templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
    agent_service = WebAgentService(
        resolved_root,
        config_context=context,
        v2_agent_service=v2_agent_service,
    )
    app = FastAPI(
        title="Stock Agent V2 Workbench",
        version="2.0.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.root = resolved_root
    app.state.agent_service = agent_service
    app.state.v2_agent_service = v2_agent_service
    app.state.v2_components = owned_components
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static"), check_dir=False), name="static")

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "app_name": context.config.app.name,
                "symbols": context.config.symbols.default,
                "default_symbol": context.config.symbols.default[0],
                "provider": context.config.provider.default,
            },
        )

    @app.get("/api/v2/health")
    def health():
        return {
            "ok": True,
            "service": "stock-agent-v2",
            "provider": context.config.provider.default,
            "model_enabled": context.config.llm.enabled,
        }

    @app.post("/api/v2/research")
    @app.post("/api/v2/research/submit", include_in_schema=False)
    def submit_research(payload: ResearchSubmitRequest, request: Request):
        try:
            return agent_service.submit_research(payload.request, actor_ref=_web_actor(request))
        except WebAgentError as exc:
            raise _research_http_error(exc) from exc

    @app.get("/api/v2/research/{task_id}")
    def research_status(task_id: str, request: Request):
        try:
            return agent_service.research_status(task_id, actor_ref=_web_actor(request))
        except WebAgentError as exc:
            raise _research_http_error(exc) from exc

    @app.post("/api/v2/research/{task_id}/input")
    def provide_research_input(task_id: str, payload: ResearchInputRequest, request: Request):
        try:
            return agent_service.provide_research_input(
                task_id,
                payload.step_id,
                payload.payload,
                actor_ref=_web_actor(request),
            )
        except WebAgentError as exc:
            raise _research_http_error(exc) from exc

    @app.post("/api/v2/research/{task_id}/pause")
    @app.post("/api/v2/research/{task_id}/resume")
    @app.post("/api/v2/research/{task_id}/cancel")
    @app.post("/api/v2/research/{task_id}/retry-report")
    def control_research(task_id: str, request: Request):
        action = request.url.path.rsplit("/", 1)[-1]
        try:
            return agent_service.control_research(task_id, action, actor_ref=_web_actor(request))
        except WebAgentError as exc:
            raise _research_http_error(exc) from exc

    @app.get("/api/v2/research/{task_id}/report")
    @app.get("/api/v2/research/{task_id}/reports/{report_id}")
    def research_report(
        task_id: str,
        request: Request,
        report_id: str | None = None,
        output_format: Literal["json", "markdown"] = Query(default="json", alias="format"),
    ):
        try:
            report = agent_service.research_report(task_id, report_id, actor_ref=_web_actor(request))
        except WebAgentError as exc:
            raise _research_http_error(exc) from exc
        if output_format == "markdown":
            return PlainTextResponse(
                render_report(FinalReport.model_validate(report), "markdown").decode("utf-8"),
                media_type="text/markdown",
            )
        return report

    @app.get("/api/v2/research/{task_id}/events")
    async def research_events(task_id: str, request: Request, once: bool = False):
        async def stream():
            while True:
                try:
                    payload = agent_service.research_status(task_id, actor_ref=_web_actor(request))
                except WebAgentError as exc:
                    payload = {"error": str(exc)}
                event_id = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                yield f"id: {event_id}\\nevent: research_status\\ndata: {json.dumps(jsonable_encoder(payload), ensure_ascii=False)}\\n\\n"
                if once or await request.is_disconnected():
                    break
                await asyncio.sleep(2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v2/research/{task_id}/diagnostics")
    def research_diagnostics(task_id: str):
        traces = AgentTraceRecorder(v2_agent_service.connection).list_task(task_id)
        budget = BudgetLedger(v2_agent_service.connection).get(task_id)
        return {
            "task_id": task_id,
            "traces": [item.model_dump(mode="json") for item in traces],
            "budget": budget.model_dump(mode="json") if budget is not None else None,
        }

    @app.get("/api/v2/input")
    def input_state():
        return agent_service.input_state()

    @app.post("/api/v2/input/heartbeat")
    def input_heartbeat(request: Request):
        return agent_service.heartbeat(actor_ref=_web_actor(request))

    @app.post("/api/v2/input/switch/requests")
    def request_input_switch(request: Request):
        try:
            return agent_service.request_input_switch(actor_ref=_web_actor(request))
        except WebAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v2/input/switch/requests/{request_id}/approve")
    @app.post("/api/v2/input/switch/requests/{request_id}/reject")
    def decide_input_switch(request_id: str, request: Request):
        try:
            return agent_service.decide_input_switch(
                request_id,
                actor_ref=_web_actor(request),
                approve=request.url.path.endswith("/approve"),
            )
        except WebAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


def _research_http_error(error: WebAgentError) -> HTTPException:
    message = str(error)
    if "is not configured" in message:
        return HTTPException(status_code=503, detail=message)
    if "does not exist" in message or "does not belong" in message:
        return HTTPException(status_code=404, detail=message)
    if "research is blocked" in message:
        return HTTPException(status_code=403, detail=message)
    if "only a" in message or "input interface" in message:
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=422, detail=message)


def _web_actor(request: Request) -> str:
    host = request.client.host if request.client is not None else "unknown"
    return f"web:{host}"


__all__ = ["ResearchInputRequest", "ResearchSubmitRequest", "create_app"]
