"""V2 command-line entry point for evidence-first research tasks."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from stock_agent import __version__
from stock_agent.commands.mcp_server import run_mcp_server
from stock_agent.commands.telegram import run_telegram
from stock_agent.commands.web import run_web
from stock_agent.commands.worker import run_worker
from stock_agent.config import init_config
from stock_agent.config_loader import load_config
from stock_agent.contracts.reports import FinalReport
from stock_agent.contracts.tasks import ResearchRequest
from stock_agent.dialog.input_gate import InputGate
from stock_agent.reports.renderers import render_report
from stock_agent.services.agent_service import AgentService
from stock_agent.services.entrypoints import ResearchEntryAdapter, ResearchEntryError
from stock_agent.services.production_v2 import ProductionV2Components, build_production_v2
from stock_agent.storage.sqlite import initialize_runtime_database
from stock_agent.worker.research_v2 import ResearchTaskWorkerV2


def _runtime_root() -> Path:
    workdir = os.getenv("STOCK_AGENT_WORKDIR")
    return Path(workdir).expanduser() if workdir else Path.cwd()


def _config_path(root: Path, args: argparse.Namespace) -> Path | None:
    configured = getattr(args, "config_path", None) or os.getenv("STOCK_AGENT_CONFIG")
    if not configured:
        return None
    path = Path(configured).expanduser()
    return path if path.is_absolute() else root / path


def _handle_init_config(args: argparse.Namespace) -> int:
    result = init_config(_runtime_root(), force=args.force, config_path=_config_path(_runtime_root(), args))
    print(f"{'created' if result.config_written else 'exists'}: {result.config_path}")
    print(f"{'created' if result.env_example_written else 'exists'}: {result.env_example_path}")
    return 0


def _handle_web(args: argparse.Namespace) -> int:
    root = _runtime_root()
    return run_web(
        root,
        host=args.host,
        port=args.port,
        config_context=load_config(root, _config_path(root, args)),
    )


def _handle_worker(args: argparse.Namespace) -> int:
    root = _runtime_root()
    return run_worker(
        root,
        once=args.once,
        interval_sec=args.interval_sec,
        config_context=load_config(root, _config_path(root, args)),
    )


def _handle_mcp_server(args: argparse.Namespace) -> int:
    root = _runtime_root()
    return run_mcp_server(root, config_context=load_config(root, _config_path(root, args)))


def _handle_telegram(args: argparse.Namespace) -> int:
    root = _runtime_root()
    return run_telegram(
        root,
        config_context=load_config(root, _config_path(root, args)),
        research_entry=args.research_entry,
    )


def _handle_research(args: argparse.Namespace) -> int:
    entry: ResearchEntryAdapter = args.research_entry
    root = _runtime_root()
    context = load_config(root, _config_path(root, args))
    action = args.action
    if action not in {"submit", "work"} and not args.task_id:
        print(f"research_status=failed\nmessage=research {action} requires TASK_ID")
        return 2
    if action == "input" and (not args.step_id or not args.payload_json):
        print("research_status=failed\nmessage=research input requires --step-id and --payload-json")
        return 2

    connection = initialize_runtime_database(root, context.config)
    actor_ref = "cli:research"
    input_actions = {"submit", "input", "pause", "resume", "cancel", "retry-report", "approve-signal"}
    try:
        if action in input_actions:
            decision = InputGate.from_config(connection, context.config.input_control).check("cli", actor_ref=actor_ref)
            if not decision.allowed:
                print(f"input_status=blocked\nmessage={decision.message}")
                return 3
        if action == "submit":
            request = ResearchRequest.model_validate_json(_request_payload(args))
            _print_status(entry.submit(request, source="cli", actor_ref=actor_ref), action="submitted")
            return 0
        if action == "work":
            worker = ResearchTaskWorkerV2(entry.service, worker_id="cli:research-work")
            tick = worker.run_task(args.task_id) if args.task_id else worker.run_once()
            for line in tick.lines():
                print(f"research_worker_{line}")
            return 0 if not tick.errors else 1
        if action in {"status", "watch"}:
            _print_status(entry.status(args.task_id, source="cli", actor_ref=actor_ref), action=action)
            return 0
        if action in {"pause", "resume", "cancel", "retry-report"}:
            _print_status(entry.control(args.task_id, action, source="cli", actor_ref=actor_ref), action=action)
            return 0
        if action == "input":
            payload = json.loads(args.payload_json)
            if not isinstance(payload, dict):
                raise ValueError("research input payload must be a JSON object")
            _print_status(
                entry.provide_input(args.task_id, args.step_id, payload, source="cli", actor_ref=actor_ref),
                action="input_received",
            )
            return 0
        if action == "report":
            report = entry.report(args.task_id, args.report_id, source="cli", actor_ref=actor_ref)
            if args.output_format == "markdown":
                print(render_report(FinalReport.model_validate(report), "markdown").decode("utf-8"))
            else:
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        if action == "approve-signal":
            if not args.signal_id or args.signal_version is None or not args.reason or not args.admin_ref:
                raise ValueError("approve-signal requires --signal-id, --signal-version, --reason, and --admin-ref")
            approval = entry.approve_signal(
                args.task_id,
                signal_id=args.signal_id,
                version=args.signal_version,
                reason=args.reason,
                source="cli",
                actor_ref=args.admin_ref,
                actor_type="human_admin",
            )
            print(json.dumps(approval, ensure_ascii=False, sort_keys=True))
            return 0
    except (ResearchEntryError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"research_status=failed\nmessage={exc}")
        return 1
    finally:
        if action in input_actions:
            InputGate.from_config(connection, context.config.input_control).mark_offline("cli", actor_ref=actor_ref)
        connection.close()
    return 2


def _request_payload(args: argparse.Namespace) -> str:
    if args.request_json:
        return args.request_json
    if args.request_file:
        return args.request_file.read_text(encoding="utf-8")
    raise ValueError("research submit requires --request-json or --request-file")


def _print_status(status: dict[str, object], *, action: str) -> None:
    task = status["task"]
    assert isinstance(task, dict)
    print(f"research_action={action}")
    print(f"task_id={task['task_id']}")
    print(f"status={task['status']}")
    print(f"report_id={status.get('report_id') or 'pending'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock-agent", description="Evidence-first market research Agent.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=False)

    init = subparsers.add_parser("init-config", help="Generate the V2 config and environment template.")
    init.add_argument("--force", action="store_true")
    init.add_argument("--config", dest="config_path", type=Path)
    init.set_defaults(handler=_handle_init_config)

    web = subparsers.add_parser("web", help="Start the V2 FastAPI workbench.")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8000)
    web.add_argument("--config", dest="config_path", type=Path)
    web.set_defaults(handler=_handle_web)

    worker = subparsers.add_parser("worker", help="Start the durable V2 research worker.")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval-sec", type=float, default=30)
    worker.add_argument("--config", dest="config_path", type=Path)
    worker.set_defaults(handler=_handle_worker)

    telegram = subparsers.add_parser("telegram", help="Start the optional V2 Telegram transport.")
    telegram.add_argument("--config", dest="config_path", type=Path)
    telegram.set_defaults(handler=_handle_telegram)

    mcp = subparsers.add_parser("mcp-server", help="Start the read-only research MCP server over stdio.")
    mcp.add_argument("--config", dest="config_path", type=Path)
    mcp.set_defaults(handler=_handle_mcp_server)

    research = subparsers.add_parser("research", help="Submit, execute, inspect, and render V2 research tasks.")
    research.add_argument("action", choices=("submit", "work", "status", "watch", "pause", "resume", "cancel", "retry-report", "input", "report", "approve-signal"))
    research.add_argument("task_id", nargs="?")
    research.add_argument("--request-json")
    research.add_argument("--request-file", type=Path)
    research.add_argument("--step-id")
    research.add_argument("--payload-json")
    research.add_argument("--report-id")
    research.add_argument("--signal-id")
    research.add_argument("--signal-version", type=int)
    research.add_argument("--reason")
    research.add_argument("--admin-ref")
    research.add_argument("--format", dest="output_format", choices=("json", "markdown"), default="markdown")
    research.add_argument("--config", dest="config_path", type=Path)
    research.set_defaults(handler=_handle_research)
    return parser


def main(argv: Sequence[str] | None = None, *, v2_agent_service: AgentService | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    owned_components: ProductionV2Components | None = None
    if args.command in {"research", "telegram"}:
        if v2_agent_service is None:
            root = _runtime_root()
            owned_components = build_production_v2(root, config_context=load_config(root, _config_path(root, args)))
            v2_agent_service = owned_components.service
        args.research_entry = ResearchEntryAdapter(v2_agent_service)
    try:
        return args.handler(args)
    finally:
        if owned_components is not None:
            owned_components.close()


if __name__ == "__main__":
    raise SystemExit(main())
