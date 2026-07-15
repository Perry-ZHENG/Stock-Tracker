"""Static policy for the small pure-Python language accepted by the signal Sandbox."""

from __future__ import annotations

import ast


class AstPolicyError(ValueError):
    """Raised when candidate source asks for a capability outside the Sandbox language."""


SAFE_CALLS = frozenset({"abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list", "max", "min", "range", "round", "sum", "tuple", "zip"})
FORBIDDEN_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "globals",
        "help",
        "input",
        "locals",
        "memoryview",
        "object",
        "open",
        "vars",
    }
)
FORBIDDEN_ATTRIBUTES = frozenset(
    {
        "__builtins__",
        "__class__",
        "__dict__",
        "__globals__",
        "__mro__",
        "__subclasses__",
        "environ",
        "getenv",
        "popen",
        "system",
    }
)


def validate_candidate_source(source: str, *, allowed_features: set[str]) -> set[str]:
    """Validate a deliberately small language before it can reach a child process."""

    try:
        module = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise AstPolicyError("candidate source is not valid Python") from exc
    if len(module.body) != 1 or not isinstance(module.body[0], ast.FunctionDef):
        raise AstPolicyError("candidate source must define exactly one function")
    function = module.body[0]
    if function.name != "compute" or len(function.args.args) != 1 or function.args.args[0].arg != "context":
        raise AstPolicyError("candidate signature must be compute(context)")
    if function.decorator_list or function.args.vararg is not None or function.args.kwarg is not None:
        raise AstPolicyError("candidate function cannot use decorators or variadic arguments")

    features: set[str] = set()
    for node in ast.walk(module):
        _validate_node(node)
        if isinstance(node, ast.Subscript) and _is_context_feature_access(node):
            if not isinstance(node.slice, ast.Constant) or not isinstance(node.slice.value, str):
                raise AstPolicyError("context.features access must use a literal feature name")
            features.add(node.slice.value)
    unknown = features - allowed_features
    if unknown:
        raise AstPolicyError(f"candidate source uses unknown features: {sorted(unknown)}")
    if not features:
        raise AstPolicyError("candidate source must access at least one FeatureCatalog feature")
    return features


def _validate_node(node: ast.AST) -> None:
    if isinstance(
        node,
        (
            ast.Import,
            ast.ImportFrom,
            ast.AsyncFunctionDef,
            ast.Await,
            ast.ClassDef,
            ast.Delete,
            ast.Global,
            ast.Lambda,
            ast.Nonlocal,
            ast.Raise,
            ast.Try,
            ast.With,
            ast.Yield,
            ast.YieldFrom,
        ),
    ):
        raise AstPolicyError("candidate source contains a forbidden executable form")
    if isinstance(node, ast.Name) and (node.id in FORBIDDEN_NAMES or node.id.startswith("__")):
        raise AstPolicyError("candidate source contains a forbidden capability")
    if isinstance(node, ast.Attribute) and (node.attr in FORBIDDEN_ATTRIBUTES or node.attr.startswith("__")):
        raise AstPolicyError("candidate source contains a forbidden attribute")
    if isinstance(node, ast.Call):
        _validate_call(node)


def _validate_call(node: ast.Call) -> None:
    if isinstance(node.func, ast.Name) and node.func.id in SAFE_CALLS:
        return
    if isinstance(node.func, ast.Attribute) and node.func.attr == "append":
        return
    raise AstPolicyError("candidate source calls a function outside the Sandbox allowlist")


def _is_context_feature_access(node: ast.Subscript) -> bool:
    return (
        isinstance(node.value, ast.Attribute)
        and node.value.attr == "features"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "context"
    )


__all__ = ["AstPolicyError", "validate_candidate_source"]
