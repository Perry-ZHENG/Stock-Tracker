"""Isolated child entrypoint. It receives JSON only and never touches task storage."""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import Any

# ``python -I`` intentionally ignores PYTHONPATH, so make this package available
# from the script's known location rather than inheriting caller configuration.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stock_agent.signal_lab.interface import SignalContext, SignalPoint  # noqa: E402


SAFE_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "max": max,
    "min": min,
    "range": range,
    "round": round,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
}


def main() -> int:
    try:
        request = _read_request()
        _apply_resource_limits(request)
        context = SignalContext.model_validate(request["context"])
        namespace: dict[str, Any] = {"__builtins__": SAFE_BUILTINS}
        exec(compile(request["source"], "<candidate>", "exec"), namespace, namespace)
        output = namespace["compute"](context)
        if not isinstance(output, list):
            return _write_error("invalid_output", "compute(context) must return a list")
        points = [SignalPoint.model_validate(point).model_dump(mode="json") for point in output]
        if len(points) > request["max_points"]:
            return _write_error("output_limit", "candidate produced too many signal points")
        serialized = json.dumps({"status": "ok", "points": points}, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) > request["max_output_bytes"]:
            return _write_error("output_limit", "candidate output exceeds the allowed size")
        sys.stdout.write(serialized)
        return 0
    except MemoryError:
        return _write_error("resource_limit", "candidate exceeded the memory limit")
    except Exception as exc:
        return _write_error("execution_error", _safe_message(exc))


def _read_request() -> dict[str, Any]:
    value = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("child request must be a JSON object")
    return value


def _apply_resource_limits(request: dict[str, Any]) -> None:
    try:
        import resource

        cpu_seconds = max(1, math.ceil(float(request["timeout_seconds"])))
        memory_bytes = int(request["memory_limit_mb"]) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        # macOS does not consistently enforce RLIMIT_AS for Python allocations;
        # RLIMIT_DATA covers the heap path used by list/dict growth there.
        for limit_name in ("RLIMIT_AS", "RLIMIT_DATA"):
            limit = getattr(resource, limit_name, None)
            if limit is not None:
                resource.setrlimit(limit, (memory_bytes, memory_bytes))
    except (ImportError, ValueError, OSError):
        # The parent timeout remains mandatory on platforms without these limits.
        return


def _write_error(code: str, message: str) -> int:
    sys.stdout.write(json.dumps({"status": "error", "code": code, "message": message}, ensure_ascii=False))
    return 1


def _safe_message(error: Exception) -> str:
    text = str(error).replace("\n", " ")
    return text[:500] or type(error).__name__


if __name__ == "__main__":  # pragma: no cover - executed in a child process
    raise SystemExit(main())
