"""Typed tool wrappers around existing Stock Agent scripts and services."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from stock_agent.config_loader import RuntimeConfigContext, load_config
from stock_agent.query import QueryService

ToolRisk = Literal["read_only", "control"]
ToolHandler = Callable[["AgentToolContext", BaseModel], dict[str, Any]]


@dataclass(frozen=True)
class AgentToolContext:
    root: Path
    config_context: RuntimeConfigContext

    @classmethod
    def load(cls, root: Path) -> "AgentToolContext":
        return cls(root=root, config_context=load_config(root))


@dataclass(frozen=True)
class AgentTool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    risk: ToolRisk = "read_only"
    requires_confirmation: bool = False

    def prompt_spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk,
            "requires_confirmation": self.requires_confirmation,
            "parameters": self.args_model.model_json_schema(),
        }

    def invoke(
        self,
        context: AgentToolContext,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        validated = self.args_model.model_validate(arguments)
        return self.handler(context, validated)


class AgentToolRegistry:
    def __init__(self, tools: list[AgentTool]) -> None:
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("agent tool names must be unique")
        self._tools = {tool.name: tool for tool in tools}

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def invoke(
        self,
        name: str,
        *,
        context: AgentToolContext,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        tool = self.get(name)
        if tool is None:
            return {
                "ok": False,
                "error": "no_suitable_tool",
                "message": f"没有注册工具: {name}",
            }
        return tool.invoke(context, arguments)

    def prompt_text(self) -> str:
        import json

        return "\n".join(
            json.dumps(tool.prompt_spec(), ensure_ascii=False, sort_keys=True)
            for tool in self._tools.values()
        )


class QuerySignalsArgs(BaseModel):
    symbol: str | None = Field(default=None, description="明确给出的股票代码，例如 QQQ")
    strategy_id: str | None = Field(
        default=None,
        description="策略标识，例如 macd；仅在用户明确指定时填写",
    )
    trading_date: date | None = Field(
        default=None,
        description="交易日期，ISO 8601 格式 YYYY-MM-DD",
    )
    limit: int = Field(default=10, ge=1, le=100)


class QueryBarsArgs(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)
    from_ts: str | None = None
    to_ts: str | None = None


class QueryLimitArgs(BaseModel):
    limit: int = Field(default=10, ge=1, le=100)


class QueryTraceArgs(BaseModel):
    target_id: str = Field(min_length=1, description="signal_id 或 trace_id")


class QueryNewsArgs(BaseModel):
    symbol: str | None = Field(default=None, description="可选股票代码")
    limit: int = Field(default=10, ge=1, le=100)


class QueryStatisticsArgs(BaseModel):
    period: Literal["day", "month", "year"] = "day"
    limit: int = Field(default=10, ge=1, le=100)


class QueryScheduleArgs(BaseModel):
    pass


class AskUserArgs(BaseModel):
    question: str = Field(min_length=1)
    missing: list[str] = Field(default_factory=list)


class NoSuitableToolArgs(BaseModel):
    reason: str = Field(min_length=1)


def build_default_tool_registry() -> AgentToolRegistry:
    return AgentToolRegistry(
        [
            # 查询已经由 Worker 和策略脚本计算、Supervisor 审核并保存的观察信号。
            # 支持按股票代码、策略名称和交易日期过滤，但不能创建新策略或新信号。
            AgentTool(
                name="query_signals",
                description=(
                    "查询已经由策略脚本产生并保存的信号；可按股票、策略和交易日期过滤。"
                    "不能创建新策略。"
                ),
                args_model=QuerySignalsArgs,
                handler=_query_signals,
            ),
            # 查询 Data Lake 中保存的历史 K 线。symbol 是必填参数；
            # 如果用户没有明确股票代码，Agent 应先调用 ask_user，而不是自行猜测。
            AgentTool(
                name="query_bars",
                description="查询某个股票在指定 UTC 时间范围内的历史 K 线。",
                args_model=QueryBarsArgs,
                handler=_query_bars,
            ),
            # 查询 Worker、行情 Provider、Supervisor 等运行模块的健康指标。
            # 这是只读诊断工具，不会启动、停止或重启任何服务。
            AgentTool(
                name="query_health",
                description="查询 Worker、Provider、Supervisor 等模块的健康状态。",
                args_model=QueryLimitArgs,
                handler=_query_health,
            ),
            # 根据 signal_id 或 trace_id 查询信号的计算、审核和数据来源追踪链。
            # target_id 必填，缺失时应调用 ask_user。
            AgentTool(
                name="query_trace",
                description="根据 signal_id 或 trace_id 查询信号计算与审核轨迹。",
                args_model=QueryTraceArgs,
                handler=_query_trace,
            ),
            # 查询市场新闻或指定股票的新闻。symbol 可选；
            # 如果新闻 Provider 未配置，工具会返回当前不可用状态或已有缓存。
            AgentTool(
                name="query_news",
                description="查询新闻；股票代码可选。",
                args_model=QueryNewsArgs,
                handler=_query_news,
            ),
            # 查询按日、月或年汇总的信号统计，不负责重新运行交易策略。
            AgentTool(
                name="query_statistics",
                description="按日、月或年查询信号统计。",
                args_model=QueryStatisticsArgs,
                handler=_query_statistics,
            ),
            # 查询交易日、休市状态和系统配置的市场监控时间窗口。
            AgentTool(
                name="query_schedule",
                description="查询交易日和当前监控时间窗口。",
                args_model=QueryScheduleArgs,
                handler=_query_schedule,
            ),
            # 查询不同行情 Provider 之间的数据质量比较结果和差异记录。
            AgentTool(
                name="query_provider_compare",
                description="查询行情供应商数据质量比较结果。",
                args_model=QueryLimitArgs,
                handler=_query_provider_compare,
            ),
            # 查询因价格、成交量、格式或质量问题而被隔离的异常行情 Bar。
            AgentTool(
                name="query_abnormal_bars",
                description="查询被隔离的异常行情 Bar。",
                args_model=QueryLimitArgs,
                handler=_query_abnormal_bars,
            ),
            # 查询待审核、已批准或已拒绝的配置修改记录；只读，不执行审批。
            AgentTool(
                name="query_config_changes",
                description="查询待审核或历史配置变更。",
                args_model=QueryLimitArgs,
                handler=_query_config_changes,
            ),
            # 对话控制工具：已经找到合适工具，但缺少必填参数或参数含义不明确时，
            # 用它向用户继续提问。它本身不会运行任何行情或策略脚本。
            AgentTool(
                name="ask_user",
                description="必填参数缺失或含义不明确时向用户追问。",
                args_model=AskUserArgs,
                handler=_ask_user,
                risk="control",
            ),
            # 对话控制工具：当前注册表中没有任何工具能够满足请求时使用。
            # 例如“新增 Order Book Imbalance 策略”目前应明确返回不支持，
            # 不能错误地改用 query_signals 或把 ORDER 当成股票代码。
            AgentTool(
                name="no_suitable_tool",
                description="当前注册工具无法完成用户请求时明确返回不支持。",
                args_model=NoSuitableToolArgs,
                handler=_no_suitable_tool,
                risk="control",
            ),
        ]
    )


def _query_signals(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QuerySignalsArgs.model_validate(raw_args.model_dump())
    result = _query(context).execute("signals", limit=100)
    rows = list(result.rows)
    if args.symbol:
        symbol = args.symbol.upper()
        rows = [row for row in rows if getattr(row, "symbol", "").upper() == symbol]
    if args.strategy_id:
        strategy_id = args.strategy_id.lower()
        rows = [
            row
            for row in rows
            if strategy_id in getattr(row, "strategy_id", "").lower()
        ]
    if args.trading_date:
        timezone = ZoneInfo(context.config_context.config.schedule.timezone)
        rows = [
            row
            for row in rows
            if getattr(row, "timestamp").astimezone(timezone).date() == args.trading_date
        ]
    rows = rows[: args.limit]
    return _tool_result(
        ok=result.ok,
        tool="query_signals",
        rows=rows,
        message=result.message,
    )


def _query_bars(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QueryBarsArgs.model_validate(raw_args.model_dump())
    result = _query(context).execute(
        "bars",
        symbol=args.symbol.upper(),
        from_value=args.from_ts,
        to_value=args.to_ts,
    )
    return _from_query_result("query_bars", result)


def _query_health(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QueryLimitArgs.model_validate(raw_args.model_dump())
    return _from_query_result(
        "query_health",
        _query(context).execute("health", limit=args.limit),
    )


def _query_trace(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QueryTraceArgs.model_validate(raw_args.model_dump())
    return _from_query_result(
        "query_trace",
        _query(context).execute("trace", target_id=args.target_id),
    )


def _query_news(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QueryNewsArgs.model_validate(raw_args.model_dump())
    return _from_query_result(
        "query_news",
        _query(context).execute(
            "news",
            symbol=args.symbol.upper() if args.symbol else None,
            limit=args.limit,
        ),
    )


def _query_statistics(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QueryStatisticsArgs.model_validate(raw_args.model_dump())
    return _from_query_result(
        "query_statistics",
        _query(context).execute("stats", period=args.period, limit=args.limit),
    )


def _query_schedule(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    QueryScheduleArgs.model_validate(raw_args.model_dump())
    return _from_query_result(
        "query_schedule",
        _query(context).execute("schedule"),
    )


def _query_provider_compare(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QueryLimitArgs.model_validate(raw_args.model_dump())
    return _from_query_result(
        "query_provider_compare",
        _query(context).execute("provider-compare", limit=args.limit),
    )


def _query_abnormal_bars(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QueryLimitArgs.model_validate(raw_args.model_dump())
    return _from_query_result(
        "query_abnormal_bars",
        _query(context).execute("abnormal-bars", limit=args.limit),
    )


def _query_config_changes(context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = QueryLimitArgs.model_validate(raw_args.model_dump())
    return _from_query_result(
        "query_config_changes",
        _query(context).execute("config-changes", limit=args.limit),
    )


def _ask_user(_context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = AskUserArgs.model_validate(raw_args.model_dump())
    return {
        "ok": False,
        "status": "needs_user_input",
        "question": args.question,
        "missing": args.missing,
    }


def _no_suitable_tool(_context: AgentToolContext, raw_args: BaseModel) -> dict[str, Any]:
    args = NoSuitableToolArgs.model_validate(raw_args.model_dump())
    return {
        "ok": False,
        "status": "no_suitable_tool",
        "message": args.reason,
    }


def _query(context: AgentToolContext) -> QueryService:
    return QueryService(
        context.root,
        config_context=context.config_context,
    )


def _from_query_result(tool: str, result) -> dict[str, Any]:
    return _tool_result(
        ok=result.ok,
        tool=tool,
        rows=result.rows,
        message=result.message,
        text=result.text,
    )


def _tool_result(
    *,
    ok: bool,
    tool: str,
    rows: list[Any],
    message: str = "",
    text: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "tool": tool,
        "count": len(rows),
        "rows": [_jsonable(row) for row in rows],
        "message": message,
        **({"text": text} if text is not None else {}),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = [
    "AgentTool",
    "AgentToolContext",
    "AgentToolRegistry",
    "AskUserArgs",
    "NoSuitableToolArgs",
    "QueryBarsArgs",
    "QueryLimitArgs",
    "QueryNewsArgs",
    "QueryScheduleArgs",
    "QuerySignalsArgs",
    "QueryStatisticsArgs",
    "QueryTraceArgs",
    "build_default_tool_registry",
]
