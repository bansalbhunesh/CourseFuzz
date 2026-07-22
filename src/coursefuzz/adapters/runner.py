"""Tiny isolated runner invoked with ``python -I`` by the sandbox adapter.

The runner accepts only a deliberately small Python subset. It is not presented as a
general-purpose multi-tenant sandbox; the Docker boundary remains the production lane.
"""

from __future__ import annotations

import ast
import json
import sys
from typing import Any

ALLOWED_NODES = {
    ast.Module,
    ast.FunctionDef,
    ast.arguments,
    ast.arg,
    ast.Return,
    ast.If,
    ast.IfExp,
    ast.For,
    ast.While,
    ast.Assign,
    ast.AugAssign,
    ast.Compare,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Load,
    ast.Store,
    ast.Constant,
    ast.Call,
    ast.Tuple,
    ast.List,
    ast.Dict,
    ast.Subscript,
    ast.Slice,
    ast.Expr,
    ast.Pass,
    ast.And,
    ast.Or,
    ast.Eq,
    ast.NotEq,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Mod,
    ast.Pow,
    ast.BitAnd,
    ast.BitOr,
    ast.USub,
}


def validate_source(source: str, entrypoint: str) -> ast.Module:
    if len(source.encode("utf-8")) > 16_384:
        raise ValueError("Program exceeds the 16 KiB demo limit")
    tree = ast.parse(source, mode="exec")
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if len(functions) != 1 or functions[0].name != entrypoint:
        raise ValueError(f"Program must define exactly one function named {entrypoint}")
    if functions[0].decorator_list:
        raise ValueError("Decorators are not permitted")
    for node in ast.walk(tree):
        if type(node) not in ALLOWED_NODES:
            raise ValueError(f"Unsupported syntax: {type(node).__name__}")
    return tree


def run(payload: dict[str, Any]) -> dict[str, Any]:
    source = str(payload["source"])
    entrypoint = str(payload["entrypoint"])
    tests = payload["tests"]
    tree = validate_source(source, entrypoint)
    globals_dict: dict[str, Any] = {
        "__builtins__": {
            "range": range,
            "len": len,
            "min": min,
            "max": max,
            "abs": abs,
            "sum": sum,
            "int": int,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
        }
    }
    exec(compile(tree, "<submission>", "exec"), globals_dict)  # noqa: S102
    function = globals_dict[entrypoint]
    outputs: list[dict[str, Any]] = []
    passed = 0
    for test in tests:
        inputs = tuple(test["inputs"])
        expected = test.get("expected")
        try:
            actual = function(*inputs)
            ok = expected is None or actual == expected
            outputs.append(
                {
                    "inputs": list(inputs),
                    "expected": expected,
                    "actual": actual,
                    "passed": ok,
                }
            )
            passed += int(ok)
        except Exception as exc:  # the exception type is evidence, not control flow
            outputs.append(
                {
                    "inputs": list(inputs),
                    "expected": expected,
                    "actual": None,
                    "passed": False,
                    "error": type(exc).__name__,
                }
            )
    return {"passed": passed, "failed": len(tests) - passed, "outputs": outputs}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
        result = run(payload)
    except ValueError as exc:
        # A restricted-language contract violation from validate_source: the program was rejected
        # before it ran. Tagged so the gateway can report ExecutionOutcome.REJECTED distinctly.
        sys.stdout.write(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}", "kind": "rejected"},
                separators=(",", ":"),
            )
        )
        return 1
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                separators=(",", ":"),
            )
        )
        return 1
    sys.stdout.write(json.dumps({"ok": True, **result}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
