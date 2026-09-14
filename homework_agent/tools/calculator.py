"""Exact arithmetic via a restricted AST evaluator.

This exists so the model checks its numbers instead of doing mental arithmetic in a
thinking block. It is deliberately not eval(): only the node types below are allowed,
so a calculate() call cannot read files, import, or call anything unlisted.
"""

from __future__ import annotations

import ast
import math
import operator
from fractions import Fraction
from typing import Any

from homework_agent.tools.base import Tool, ToolError

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_COMPARE = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": lambda *args: sum(args[0]) if len(args) == 1 else sum(args),
    "fraction": lambda n, d=1: Fraction(n, d),
    # math
    "sqrt": math.sqrt,
    "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "exp": math.exp,
    "log": math.log,  # log(x) natural, log(x, base) for any base
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "degrees": math.degrees,
    "radians": math.radians,
    "floor": math.floor,
    "ceil": math.ceil,
    "trunc": math.trunc,
    "factorial": math.factorial,
    "gcd": math.gcd,
    "lcm": math.lcm,
    "comb": math.comb,
    "perm": math.perm,
    "hypot": math.hypot,
    "isqrt": math.isqrt,
}

_CONSTANTS: dict[str, Any] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
}

# Guard rails: ** on large ints can hang the process before any timeout fires.
_MAX_EXPONENT = 10_000
_MAX_FACTORIAL = 2_000


def evaluate(expression: str) -> Any:
    """Evaluate an arithmetic expression, or raise ToolError."""
    if not expression or not expression.strip():
        raise ToolError("expression is empty")
    if len(expression) > 2_000:
        raise ToolError("expression too long (limit 2000 characters)")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"could not parse expression: {exc.msg}") from None
    return _eval(tree.body)


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)) and not isinstance(node.value, bool):
            return node.value
        raise ToolError(f"unsupported constant {node.value!r}; calculate handles numbers only")

    if isinstance(node, ast.BinOp):
        op = _BINARY.get(type(node.op))
        if op is None:
            raise ToolError(f"unsupported operator {type(node.op).__name__}")
        left, right = _eval(node.left), _eval(node.right)
        if op is operator.pow:
            _check_pow(left, right)
        try:
            return op(left, right)
        except ZeroDivisionError:
            raise ToolError("division by zero") from None
        except (OverflowError, ValueError) as exc:
            raise ToolError(f"arithmetic error: {exc}") from None

    if isinstance(node, ast.UnaryOp):
        op = _UNARY.get(type(node.op))
        if op is None:
            raise ToolError(f"unsupported unary operator {type(node.op).__name__}")
        return op(_eval(node.operand))

    if isinstance(node, ast.Compare):
        # Lets the model verify an identity: calculate("2**10 == 1024") -> True
        left = _eval(node.left)
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _COMPARE.get(type(op_node))
            if op is None:
                raise ToolError(f"unsupported comparison {type(op_node).__name__}")
            right = _eval(comparator)
            if not op(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ToolError("only plain function calls like sqrt(2) are supported")
        func = _FUNCTIONS.get(node.func.id)
        if func is None:
            raise ToolError(
                f"unknown function {node.func.id!r}; available: {', '.join(sorted(_FUNCTIONS))}"
            )
        if node.keywords:
            raise ToolError("keyword arguments are not supported in calculate")
        args = [_eval(arg) for arg in node.args]
        if node.func.id == "factorial" and args and args[0] > _MAX_FACTORIAL:
            raise ToolError(f"factorial argument too large (limit {_MAX_FACTORIAL})")
        try:
            return func(*args)
        except (ValueError, TypeError, OverflowError, ZeroDivisionError) as exc:
            raise ToolError(f"{node.func.id}: {exc}") from None

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise ToolError(f"unknown name {node.id!r}; calculate has no variables, substitute values")

    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval(element) for element in node.elts]

    raise ToolError(f"unsupported expression element {type(node).__name__}")


def _check_pow(base: Any, exponent: Any) -> None:
    if isinstance(exponent, (int, float)) and abs(exponent) > _MAX_EXPONENT:
        raise ToolError(f"exponent too large (limit {_MAX_EXPONENT})")
    if isinstance(base, int) and isinstance(exponent, int) and exponent > 0:
        digits = (len(str(abs(base))) or 1) * exponent
        if digits > 100_000:
            raise ToolError("result would have too many digits to be useful")


def format_result(value: Any) -> str:
    """Render a result usefully: exact value plus a decimal when they differ."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Fraction):
        return f"{value} (= {float(value):.10g})"
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return f"{value:.10g} (exactly {int(value)})"
        return f"{value:.12g}"
    if isinstance(value, list):
        return ", ".join(format_result(item) for item in value)
    return str(value)


class CalculateTool(Tool):
    name = "calculate"
    description = (
        "Evaluate an arithmetic expression exactly. Use this for every numeric step you would "
        "otherwise do in your head, and to check an answer by substituting it back. Supports "
        "+ - * / // % **, comparisons, and functions: sqrt, cbrt, exp, log(x[, base]), log2, "
        "log10, sin, cos, tan, asin, acos, atan, atan2, sinh, cosh, tanh, degrees, radians, "
        "floor, ceil, trunc, abs, round, min, max, sum, factorial, gcd, lcm, comb, perm, hypot, "
        "isqrt, fraction(n, d). Constants: pi, e, tau. Trig takes radians - wrap degrees with "
        "radians(). There are no variables: substitute numbers first. For anything this cannot "
        "express (solving, symbolic algebra, simulation), use run_python."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The expression, e.g. '(-3 + sqrt(3**2 - 4*2*1)) / (2*2)'.",
            }
        },
        "required": ["expression"],
    }

    def run(self, expression: str) -> str:  # type: ignore[override]
        return format_result(evaluate(expression))
