"""Runtime configuration loading and reload context."""

from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stock_agent.config import DEFAULT_CONFIG, StockAgentConfig, validate_config


@dataclass(frozen=True)
class RuntimeConfigContext:
    """Immutable snapshot of the config currently used by runtime components."""

    config: StockAgentConfig
    raw_config: dict[str, Any]
    config_path: Path
    loaded_at: datetime
    version: str
    used_defaults: bool = False


def load_config(root: Path, config_path: Path | None = None) -> RuntimeConfigContext:
    """Load config from env, explicit path, or ``configs/config.yaml``.

    If the default config file does not exist yet, use DEFAULT_CONFIG so demo and
    tests can still boot before ``stock-agent init-config`` has been run.
    """

    resolved_path = _resolve_config_path(root, config_path)
    if resolved_path.exists():
        text = resolved_path.read_text(encoding="utf-8")
        raw_config = _load_yaml_mapping(text)
        used_defaults = False
    else:
        text = _stable_repr(DEFAULT_CONFIG)
        raw_config = deepcopy(DEFAULT_CONFIG)
        used_defaults = True

    config = validate_config(raw_config)
    return RuntimeConfigContext(
        config=config,
        raw_config=raw_config,
        config_path=resolved_path,
        loaded_at=datetime.now(UTC),
        version=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        used_defaults=used_defaults,
    )


def reload_config(
    current: RuntimeConfigContext,
    *,
    root: Path,
    config_path: Path | None = None,
) -> RuntimeConfigContext:
    """Return a new config snapshot; callers keep ``current`` if this raises."""

    return load_config(root, config_path or current.config_path)


def _resolve_config_path(root: Path, config_path: Path | None) -> Path:
    env_path = os.getenv("STOCK_AGENT_CONFIG")
    path = Path(env_path).expanduser() if env_path and config_path is None else config_path
    if path is None:
        path = root / "configs" / "config.yaml"
    elif not path.is_absolute():
        path = root / path
    return path


def _load_yaml_mapping(text: str) -> dict[str, Any]:
    import yaml

    class ProjectSafeLoader(yaml.SafeLoader):
        pass

    def construct_project_int(loader, node):
        scalar = loader.construct_scalar(node)
        if ":" in scalar:
            return scalar
        return yaml.constructor.SafeConstructor.construct_yaml_int(loader, node)

    ProjectSafeLoader.add_constructor("tag:yaml.org,2002:int", construct_project_int)
    value = yaml.load(text, Loader=ProjectSafeLoader)

    if not isinstance(value, dict):
        raise ValueError("config YAML root must be a mapping")
    return value


def _parse_project_yaml(text: str) -> dict[str, Any]:
    lines = _normalized_yaml_lines(text)
    if not lines:
        return {}
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(f"unexpected YAML content at line {index + 1}")
    if not isinstance(value, dict):
        raise ValueError("config YAML root must be a mapping")
    return value


def _normalized_yaml_lines(text: str) -> list[tuple[int, str]]:
    normalized: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        normalized.append((indent, raw_line.strip()))
    return normalized


def _parse_block(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[Any, int]:
    if lines[index][1].startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ValueError(f"unexpected indentation near: {content}")
        if content.startswith("- "):
            break
        key, separator, rest = content.partition(":")
        if not separator:
            raise ValueError(f"expected mapping entry near: {content}")
        key = key.strip()
        rest = rest.strip()
        if rest:
            result[key] = _parse_scalar(rest)
            index += 1
            continue
        index += 1
        if index >= len(lines) or lines[index][0] <= indent:
            result[key] = None
            continue
        result[key], index = _parse_block(lines, index, lines[index][0])
    return result, index


def _parse_list(
    lines: list[tuple[int, str]],
    index: int,
    indent: int,
) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(lines):
        line_indent, content = lines[index]
        if line_indent < indent:
            break
        if line_indent > indent:
            raise ValueError(f"unexpected indentation near: {content}")
        if not content.startswith("- "):
            break
        rest = content[2:].strip()
        index += 1
        if rest:
            result.append(_parse_scalar(rest))
            continue
        if index >= len(lines) or lines[index][0] <= indent:
            result.append(None)
            continue
        value, index = _parse_block(lines, index, lines[index][0])
        result.append(value)
    return result, index


def _parse_scalar(value: str) -> Any:
    if value == "[]":
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(item.strip()) for item in inner.split(",")]
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value.strip("\"'")


def _stable_repr(value: Any) -> str:
    return repr(value)


__all__ = ["RuntimeConfigContext", "load_config", "reload_config"]
