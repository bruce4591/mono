from __future__ import annotations

import ast
import operator
from typing import Callable


ALLOWED_INDICATOR_NAMES = {
    "last_price",
    "change_pct",
    "volume_raw",
    "turnover_raw",
}

ALLOWED_INDICATOR_FUNCTIONS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "min": min,
    "max": max,
}

_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}

_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def evaluate_indicator_expression(
    expression: str,
    values: dict[str, float],
) -> float:
    parsed = ast.parse(expression, mode="eval")
    return float(_evaluate_node(parsed.body, values))


def _evaluate_node(node: ast.AST, values: dict[str, float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int | float):
            return float(node.value)
        raise ValueError("indicator constants must be numeric")
    if isinstance(node, ast.Name):
        if node.id not in ALLOWED_INDICATOR_NAMES:
            raise ValueError(f"indicator name is not allowed: {node.id}")
        value = values.get(node.id)
        if value is None:
            raise ValueError(f"indicator value is missing: {node.id}")
        return float(value)
    if isinstance(node, ast.BinOp):
        operator_fn = _BINARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError("indicator operator is not allowed")
        return float(operator_fn(_evaluate_node(node.left, values), _evaluate_node(node.right, values)))
    if isinstance(node, ast.UnaryOp):
        operator_fn = _UNARY_OPERATORS.get(type(node.op))
        if operator_fn is None:
            raise ValueError("indicator unary operator is not allowed")
        return float(operator_fn(_evaluate_node(node.operand, values)))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("indicator function is not allowed")
        function = ALLOWED_INDICATOR_FUNCTIONS.get(node.func.id)
        if function is None:
            raise ValueError(f"indicator function is not allowed: {node.func.id}")
        args = [_evaluate_node(arg, values) for arg in node.args]
        return float(function(*args))
    raise ValueError("indicator expression node is not allowed")
