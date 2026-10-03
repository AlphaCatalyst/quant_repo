"""Restricted scalar arithmetic for thesis valuations."""

import ast
import math
import operator
from collections.abc import Mapping


class FormulaError(ValueError):
    """Invalid or uncomputable valuation expression."""


_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}
_FUNCTIONS = {"min": min, "max": max, "abs": abs}


def _walk(node: ast.AST, names: set[str]) -> set[str]:
    if isinstance(node, ast.Expression):
        return _walk(node.body, names)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return set()
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in names:
        return {node.id}
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _walk(node.left, names) | _walk(node.right, names)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _walk(node.operand, names)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS and not node.keywords and node.args):
        if node.func.id == "abs" and len(node.args) != 1:
            raise FormulaError("abs requires one argument")
        result = set()
        for arg in node.args:
            result |= _walk(arg, names)
        return result
    raise FormulaError(f"disallowed expression: {ast.dump(node, include_attributes=False)}")


def references(formula: str, names: set[str]) -> set[str]:
    """Validate a formula and return the parameter names it uses."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"invalid formula syntax: {exc.msg}") from None
    return _walk(tree, names)


def _value(node: ast.AST, values: Mapping[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _value(node.body, values)
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return float(values[node.id])
    if isinstance(node, ast.BinOp):
        return _BINARY[type(node.op)](_value(node.left, values), _value(node.right, values))
    if isinstance(node, ast.UnaryOp):
        value = _value(node.operand, values)
        return -value if isinstance(node.op, ast.USub) else value
    return float(_FUNCTIONS[node.func.id](*(_value(arg, values) for arg in node.args)))


def evaluate(formula: str, values: Mapping[str, float]) -> float:
    """Evaluate only the arithmetic grammar checked by :func:`references`."""
    references(formula, set(values))
    try:
        result = float(_value(ast.parse(formula, mode="eval"), values))
    except (ArithmeticError, OverflowError, TypeError, ValueError) as exc:
        raise FormulaError(f"formula cannot be evaluated: {exc}") from None
    if not math.isfinite(result):
        raise FormulaError("formula result must be finite")
    return result
