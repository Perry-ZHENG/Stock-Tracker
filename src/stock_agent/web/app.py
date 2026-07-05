"""FastAPI application for the local Stock Agent workbench."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.dialog.input_gate import InputGate
from stock_agent.dialog.llm_parser import LlmParser
from stock_agent.query import QueryService
from stock_agent.storage.repositories import (
    list_agent_runs,
    list_config_changes,
    list_health_metrics,
    list_signals,
)
from stock_agent.storage.sqlite import open_database
from stock_agent.web.agent_service import WebAgentError, WebAgentService

WEB_ROOT = Path(__file__).resolve().parent


class AgentPlanRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def create_app(
    root: Path,
    *,
    config_context: RuntimeConfigContext | None = None,
    llm_parser: LlmParser | None = None,
) -> FastAPI:
    resolved_root = root.resolve()
    context = config_context or load_config(resolved_root)
    templates = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
    agent_service = WebAgentService(
        resolved_root,
        config_context=context,
        llm_parser=llm_parser,
    )

    app = FastAPI(
        title="Stock Agent Workbench",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.root = resolved_root
    app.state.config_context = context
    app.state.agent_service = agent_service
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

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
                "poll_interval": context.config.provider.twelve_data.poll_interval_sec,
            },
        )

    @app.get("/api/v1/bars")
    def bars(
        symbol: str = Query(min_length=1, max_length=12),
        from_value: str | None = Query(default=None, alias="from"),
        to_value: str | None = Query(default=None, alias="to"),
    ):
        result = QueryService(resolved_root, config_context=context).execute(
            "bars",
            symbol=symbol.upper(),
            from_value=from_value,
            to_value=to_value,
        )
        return _query_response(result)

    @app.get("/api/v1/signals")
    def signals(limit: int = Query(default=20, ge=1, le=100)):
        result = QueryService(resolved_root, config_context=context).execute(
            "signals",
            limit=limit,
        )
        return _query_response(result)

    @app.get("/api/v1/signals/{signal_id}/trace")
    def signal_trace(signal_id: str):
        result = QueryService(resolved_root, config_context=context).execute(
            "trace",
            target_id=signal_id,
        )
        return _query_response(result, status_code=200 if result.ok else 404)

    @app.get("/api/v1/health")
    def health(limit: int = Query(default=20, ge=1, le=100)):
        result = QueryService(resolved_root, config_context=context).execute(
            "health",
            limit=limit,
        )
        return _query_response(result)

    @app.get("/api/v1/config-changes")
    def config_changes(limit: int = Query(default=20, ge=1, le=100)):
        result = QueryService(resolved_root, config_context=context).execute(
            "config-changes",
            limit=limit,
        )
        return _query_response(result)

    @app.post("/api/v1/agent/plan")
    def agent_plan(payload: AgentPlanRequest, request: Request):
        try:
            return agent_service.plan(payload.message, actor_ref=_web_actor(request))
        except WebAgentError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/v1/agent/runs/{run_id}/confirm")
    def agent_confirm(run_id: str, request: Request):
        try:
            return agent_service.confirm(run_id, actor_ref=_web_actor(request))
        except WebAgentError as exc:
            status_code = 404 if "not found" in str(exc) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @app.get("/api/v1/input")
    def input_state():
        return agent_service.input_state()

    @app.post("/api/v1/input/heartbeat")
    def input_heartbeat(request: Request):
        return agent_service.heartbeat(actor_ref=_web_actor(request))

    @app.post("/api/v1/input/switch/requests")
    def request_input_switch(request: Request):
        try:
            return agent_service.request_input_switch(actor_ref=_web_actor(request))
        except WebAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/input/switch/requests/{request_id}/approve")
    def approve_input_switch(request_id: str, request: Request):
        try:
            return agent_service.decide_input_switch(
                request_id,
                actor_ref=_web_actor(request),
                approve=True,
            )
        except WebAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/v1/input/switch/requests/{request_id}/reject")
    def reject_input_switch(request_id: str, request: Request):
        try:
            return agent_service.decide_input_switch(
                request_id,
                actor_ref=_web_actor(request),
                approve=False,
            )
        except WebAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/v1/events")
    async def events(request: Request, once: bool = False):
        async def stream():
            while True:
                agent_service.heartbeat(actor_ref=_web_actor(request))
                payload = _event_snapshot(resolved_root, context)
                event_id = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                yield f"id: {event_id}\nevent: snapshot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if once or await request.is_disconnected():
                    break
                await asyncio.sleep(5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/partials/signals")
    def signals_partial(request: Request):
        result = QueryService(resolved_root, config_context=context).execute("signals", limit=8)
        return templates.TemplateResponse(
            request=request,
            name="signals.html",
            context={"rows": result.rows, "ok": result.ok, "message": result.message},
        )

    @app.get("/partials/health")
    def health_partial(request: Request):
        result = QueryService(resolved_root, config_context=context).execute("health", limit=8)
        return templates.TemplateResponse(
            request=request,
            name="health.html",
            context={"rows": result.rows, "ok": result.ok, "message": result.message},
        )

    @app.get("/partials/config-changes")
    def changes_partial(request: Request):
        result = QueryService(resolved_root, config_context=context).execute(
            "config-changes",
            limit=8,
        )
        return templates.TemplateResponse(
            request=request,
            name="changes.html",
            context={"rows": result.rows, "ok": result.ok, "message": result.message},
        )

    @app.post("/partials/agent")
    def agent_partial(request: Request, message: str = Form(min_length=1, max_length=4000)):
        try:
            run = agent_service.plan(message, actor_ref=_web_actor(request))
        except WebAgentError as exc:
            run = {"status": "failed", "output": str(exc), "requires_confirmation": False}
        return templates.TemplateResponse(
            request=request,
            name="agent_result.html",
            context={"run": run},
        )

    @app.post("/partials/agent/{run_id}/confirm")
    def agent_confirm_partial(request: Request, run_id: str):
        try:
            run = agent_service.confirm(run_id, actor_ref=_web_actor(request))
        except WebAgentError as exc:
            run = {"status": "failed", "output": str(exc), "requires_confirmation": False}
        return templates.TemplateResponse(
            request=request,
            name="agent_result.html",
            context={"run": run},
        )

    return app


def _query_response(result, *, status_code: int = 200) -> JSONResponse:
    payload = {
        "ok": result.ok,
        "query": result.query,
        "rows": jsonable_encoder(result.rows),
        "message": result.message,
        "text": result.text,
    }
    resolved_status = status_code if result.ok else max(status_code, 200)
    return JSONResponse(payload, status_code=resolved_status)


def _event_snapshot(root: Path, context: RuntimeConfigContext) -> dict[str, object]:
    sqlite_path = root / context.config.storage.sqlite_path
    if not sqlite_path.exists():
        return {
            "signals": [],
            "health": [],
            "agent_runs": [],
            "input_control": {
                "active_source": None,
                "active_online": False,
                "pending_requests": [],
            },
            "generated_at": datetime.now(UTC),
        }
    connection = open_database(sqlite_path)
    try:
        return jsonable_encoder(
            {
                "signals": list_signals(connection, limit=3),
                "health": list_health_metrics(connection, limit=3),
                "agent_runs": list_agent_runs(connection, limit=3),
                "input_control": InputGate.from_config(
                    connection,
                    context.config.input_control,
                ).state().as_dict(),
                "generated_at": datetime.now(UTC),
            }
        )
    finally:
        connection.close()


def _web_actor(request: Request) -> str:
    host = request.client.host if request.client is not None else "unknown"
    return f"web:{host}"


__all__ = ["AgentPlanRequest", "create_app"]
