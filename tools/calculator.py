"""Safe exact arithmetic helper for agent tool use.

The extraction agents often need to verify financial-statement subtotals.
This module intentionally supports only a tiny arithmetic grammar: decimal
numbers, parentheses, unary +/- and the four basic operators. It never uses
``eval`` and rejects names, calls, attributes, exponentiation, and any other
Python syntax.
"""
from __future__ import annotations

import ast
import json
import re
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext


class CalculatorError(ValueError):
    """Raised when an expression is outside the calculator's safe grammar."""


def calculate(expression: str) -> Decimal:
    """Evaluate a simple arithmetic expression exactly with ``Decimal``.

    Supports:
      - integers and decimals, including thousands separators (``1,234.56``)
      - parentheses
      - unary ``+`` / ``-``
      - binary ``+`` / ``-`` / ``*`` / ``/``

    Accounting parentheses are deliberately not treated as negatives because
    they conflict with ordinary arithmetic grouping. Use ``-123`` for a
    negative amount.
    """
    cleaned = _normalise_expression(expression)
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise CalculatorError(f"Invalid arithmetic expression: {exc.msg}") from exc

    with localcontext() as ctx:
        ctx.prec = 50
        return +_eval_node(tree.body, cleaned)


def calculator_result_json(expression: str) -> str:
    """Return a JSON tool result with either ``result`` or ``error``."""
    try:
        result = calculate(expression)
    except CalculatorError as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"result": _format_decimal(result)})


def calculator_batch_json(expressions: list[str]) -> str:
    """Evaluate many expressions in one call; return a JSON array of results.

    Each element is ``{"expression": ..., "result": ...}`` on success or
    ``{"expression": ..., "error": ...}`` on failure. Errors are isolated
    per item — one bad expression does NOT fail the whole batch — so the agent
    can verify many subtotals in a single turn (instead of one tool call per
    turn, which burns the iteration budget). A non-list or empty input returns
    a single structured error rather than raising.
    """
    if not isinstance(expressions, list) or not expressions:
        return json.dumps(
            {"error": "Pass a non-empty list of expressions, e.g. ['1+2', '10-3']."}
        )
    out: list[dict[str, str]] = []
    for expr in expressions:
        try:
            value = calculate(expr)
        except CalculatorError as exc:
            out.append({"expression": str(expr), "error": str(exc)})
        else:
            out.append({"expression": str(expr), "result": _format_decimal(value)})
    return json.dumps(out)


def _normalise_expression(expression: str) -> str:
    if not isinstance(expression, str) or not expression.strip():
        raise CalculatorError("Expression must be a non-empty string.")
    # Financial statements commonly use thousands separators. Remove commas
    # only when they sit between digits; everything else remains invalid.
    return re.sub(r"(?<=\d),(?=\d)", "", expression.strip())


def _eval_node(node: ast.AST, source: str) -> Decimal:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("Only numeric literals are allowed.")
        raw = ast.get_source_segment(source, node)
        if raw is None:
            raw = str(node.value)
        try:
            return Decimal(raw)
        except InvalidOperation as exc:
            raise CalculatorError(f"Invalid number literal: {raw!r}") from exc

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, source)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise CalculatorError("Only unary + and - are allowed.")

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, source)
        right = _eval_node(node.right, source)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise CalculatorError("Division by zero.")
            try:
                return left / right
            except DivisionByZero as exc:
                raise CalculatorError("Division by zero.") from exc
        raise CalculatorError("Only +, -, * and / operators are allowed.")

    raise CalculatorError("Expression may contain only numbers and arithmetic operators.")


def _format_decimal(value: Decimal) -> str:
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    return format(value.normalize(), "f")
