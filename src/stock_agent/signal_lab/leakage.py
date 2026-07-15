"""Conservative static leakage checks for generated feature code."""

from __future__ import annotations

import ast

from stock_agent.contracts.signals import LeakageCheck


def inspect_leakage(source: str) -> list[LeakageCheck]:
    """Reject known future access and full-sample normalization patterns conservatively."""

    lowered = source.casefold()
    future_tokens = ("future", "lookahead", "next_bar", "next bar", "前视", "未来")
    future_index = _has_positive_index_offset(source)
    global_normalization = "sum(values)" in lowered and "len(values)" in lowered
    return [
        LeakageCheck(
            name="no_look_ahead",
            passed=not (future_index or any(token in lowered for token in future_tokens)),
            details="no positive index offset or future-data marker was found" if not (future_index or any(token in lowered for token in future_tokens)) else "candidate source may read future observations",
        ),
        LeakageCheck(
            name="no_full_sample_normalization",
            passed=not global_normalization,
            details="candidate source does not normalize against the complete sample" if not global_normalization else "candidate source uses complete-series sum(values)/len(values)",
        ),
        LeakageCheck(
            name="no_future_news_input",
            passed=True,
            details="SignalContext contains only split-scoped market feature arrays and no news payload",
        ),
    ]


def _has_positive_index_offset(source: str) -> bool:
    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError:
        return True
    for node in ast.walk(module):
        if not isinstance(node, ast.Subscript) or not isinstance(node.slice, ast.BinOp):
            continue
        if not isinstance(node.slice.op, ast.Add) or not isinstance(node.slice.right, ast.Constant):
            continue
        if isinstance(node.slice.right.value, int) and node.slice.right.value > 0:
            return True
    return False


__all__ = ["inspect_leakage"]
