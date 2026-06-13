"""Configuration defaults, validation, and initialization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "name": "stock-agent",
        "env": "demo",
        "timezone": "America/New_York",
    },
    "provider": {
        "default": "csv_demo",
        "priority": ["csv_demo"],
        "fallback": {
            "enabled": True,
            "order": ["csv_demo"],
        },
        "csv_demo": {
            "path": "data/sample/sample_bars.csv",
        },
        "live": {
            "name": "placeholder",
            "api_key_env": "MARKET_DATA_API_KEY",
        },
    },
    "symbols": {
        "default": ["AAPL", "MSFT", "NVDA"],
    },
    "bar": {
        "interval": "30m",
        "session": "regular_only",
        "include_pre_market": False,
        "include_after_hours": False,
    },
    "schedule": {
        "timezone": "America/New_York",
        "regular_session_start": "09:30",
        "regular_session_end": "16:00",
        "premarket_lead_minutes": 60,
        "close_focus_window_minutes": 60,
        "afterhours_tail_minutes": 60,
    },
    "strategies": {
        "ma_cross": {
            "enabled": True,
            "pairs": [[3, 5], [5, 10], [10, 20]],
        },
        "boll": {
            "enabled": True,
            "window": 20,
            "bandwidth_baseline_window": 20,
        },
        "macd": {
            "enabled": False,
            "fast": 12,
            "slow": 26,
            "signal": 9,
        },
        "kdj": {
            "enabled": False,
            "window": 9,
        },
        "active_j": {
            "enabled": False,
            "j_threshold": 20.0,
            "ma_window": 80,
            "kdj_window": 9,
            "k_smoothing": 3,
            "d_smoothing": 3,
        },
    },
    "telegram": {
        "enabled": False,
        "token_env": "TELEGRAM_BOT_TOKEN",
        "allowed_user_ids": [],
    },
    "news": {
        "enabled": True,
        "mode": "on_demand",
        "provider": "placeholder",
        "api_key_env": "NEWS_API_KEY",
        "cache_ttl_minutes": 30,
    },
    "llm": {
        "enabled": False,
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
    "storage": {
        "sqlite_path": "data/runtime/stock_agent.sqlite",
        "parquet_root": "data/lake",
        "duckdb_path": "data/analytics/stock_agent.duckdb",
    },
    "health": {
        "heartbeat_timeout_sec": 300,
        "data_delay_degraded_sec": 60,
        "data_delay_unhealthy_sec": 300,
        "error_rate_degraded": 0.01,
        "error_rate_unhealthy": 0.05,
        "consecutive_failure_unhealthy": 3,
    },
}

DEFAULT_ENV_EXAMPLE = """MARKET_DATA_API_KEY=
TELEGRAM_BOT_TOKEN=
NEWS_API_KEY=
OPENAI_API_KEY=
"""


class AppConfig(BaseModel):
    name: str
    env: str
    timezone: str


class CsvDemoProviderConfig(BaseModel):
    path: str


class LiveProviderConfig(BaseModel):
    name: str
    api_key_env: str


class ProviderFallbackConfig(BaseModel):
    enabled: bool = True
    order: list[str] = Field(default_factory=lambda: ["csv_demo"])


class ProviderConfig(BaseModel):
    default: str
    priority: list[str] = Field(default_factory=list)
    fallback: ProviderFallbackConfig = Field(default_factory=ProviderFallbackConfig)
    csv_demo: CsvDemoProviderConfig
    live: LiveProviderConfig


class SymbolsConfig(BaseModel):
    default: list[str] = Field(min_length=1)


class BarConfig(BaseModel):
    interval: str
    session: Literal["regular_only"]
    include_pre_market: bool
    include_after_hours: bool


class ScheduleConfig(BaseModel):
    timezone: str = "America/New_York"
    regular_session_start: str = "09:30"
    regular_session_end: str = "16:00"
    premarket_lead_minutes: int = Field(ge=0)
    close_focus_window_minutes: int = Field(ge=0)
    afterhours_tail_minutes: int = Field(ge=0)


class MaCrossConfig(BaseModel):
    enabled: bool
    pairs: list[tuple[int, int]]


class BollConfig(BaseModel):
    enabled: bool
    window: int = Field(gt=0)
    bandwidth_baseline_window: int = Field(gt=0)


class MacdConfig(BaseModel):
    enabled: bool
    fast: int = Field(gt=0)
    slow: int = Field(gt=0)
    signal: int = Field(gt=0)


class KdjConfig(BaseModel):
    enabled: bool
    window: int = Field(gt=0)
    k_smoothing: int = Field(default=3, gt=0)
    d_smoothing: int = Field(default=3, gt=0)


class ActiveJConfig(BaseModel):
    enabled: bool
    j_threshold: float = Field(ge=0)
    ma_window: int = Field(gt=0)
    kdj_window: int = Field(gt=0)
    k_smoothing: int = Field(gt=0)
    d_smoothing: int = Field(gt=0)


class StrategiesConfig(BaseModel):
    ma_cross: MaCrossConfig
    boll: BollConfig
    macd: MacdConfig
    kdj: KdjConfig
    active_j: ActiveJConfig = Field(default_factory=lambda: ActiveJConfig.model_validate(DEFAULT_CONFIG["strategies"]["active_j"]))


class TelegramConfig(BaseModel):
    enabled: bool
    token_env: str
    allowed_user_ids: list[int]


class NewsConfig(BaseModel):
    enabled: bool
    mode: Literal["on_demand"]
    provider: str
    api_key_env: str
    cache_ttl_minutes: int = Field(gt=0)


class LlmConfig(BaseModel):
    enabled: bool
    provider: str
    model: str
    api_key_env: str


class StorageConfig(BaseModel):
    sqlite_path: str
    parquet_root: str
    duckdb_path: str


class HealthConfig(BaseModel):
    heartbeat_timeout_sec: int = Field(gt=0)
    data_delay_degraded_sec: int = Field(gt=0)
    data_delay_unhealthy_sec: int = Field(gt=0)
    error_rate_degraded: float = Field(ge=0, lt=1)
    error_rate_unhealthy: float = Field(ge=0, lt=1)
    consecutive_failure_unhealthy: int = Field(gt=0)


class StockAgentConfig(BaseModel):
    app: AppConfig
    provider: ProviderConfig
    symbols: SymbolsConfig
    bar: BarConfig
    schedule: ScheduleConfig = Field(default_factory=lambda: ScheduleConfig.model_validate(DEFAULT_CONFIG["schedule"]))
    strategies: StrategiesConfig
    telegram: TelegramConfig
    news: NewsConfig
    llm: LlmConfig
    storage: StorageConfig
    health: HealthConfig


@dataclass(frozen=True)
class InitConfigResult:
    config_path: Path
    env_example_path: Path
    config_written: bool
    env_example_written: bool


def validate_config(config: dict[str, Any]) -> StockAgentConfig:
    try:
        return StockAgentConfig.model_validate(config)
    except ValidationError as exc:
        raise ValueError(f"Invalid Stock Agent config: {exc}") from exc


def default_config_yaml() -> str:
    validate_config(DEFAULT_CONFIG)
    return _to_yaml(DEFAULT_CONFIG)


def render_config_yaml(config: dict[str, Any]) -> str:
    validate_config(config)
    return _to_yaml(config)


def init_config(root: Path, force: bool = False, config_path: Path | None = None) -> InitConfigResult:
    validate_config(DEFAULT_CONFIG)
    config_path = config_path or root / "configs" / "config.yaml"
    if not config_path.is_absolute():
        config_path = root / config_path
    env_example_path = root / ".env.example"

    config_written = _write_text(config_path, default_config_yaml(), force=force)
    env_example_written = _write_text(env_example_path, DEFAULT_ENV_EXAMPLE, force=force)

    return InitConfigResult(
        config_path=config_path,
        env_example_path=env_example_path,
        config_written=config_written,
        env_example_written=env_example_written,
    )


def _write_text(path: Path, content: str, force: bool) -> bool:
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def _to_yaml(value: Any, indent: int = 0) -> str:
    lines = list(_yaml_lines(value, indent))
    return "\n".join(lines) + "\n"


def _yaml_lines(value: Any, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{prefix}{key}:")
                lines.extend(_yaml_lines(item, indent + 2))
            elif isinstance(item, list):
                lines.append(f"{prefix}{key}: []")
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if _is_scalar_list(item):
                lines.append(f"{prefix}- {_yaml_inline_list(item)}")
            elif isinstance(item, dict):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent + 2))
            elif isinstance(item, list):
                lines.append(f"{prefix}-")
                lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _is_scalar_list(value: Any) -> bool:
    return isinstance(value, list) and all(not isinstance(item, (dict, list)) for item in value)


def _yaml_inline_list(values: list[Any]) -> str:
    return "[" + ", ".join(_yaml_scalar(item) for item in values) + "]"


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return str(value)
