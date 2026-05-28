import json

import pytest

from tools.calculator import CalculatorError, calculate, calculator_result_json


def test_calculator_exact_addition_and_subtraction():
    assert calculate("19234567 + 8923456 - 1023456") == 27134567


def test_calculator_uses_decimal_not_binary_float():
    payload = json.loads(calculator_result_json("0.1 + 0.2"))
    assert payload == {"result": "0.3"}


def test_calculator_accepts_commas_parentheses_and_multiplication():
    payload = json.loads(calculator_result_json("(1,234 + 66) * 2"))
    assert payload == {"result": "2600"}


def test_calculator_rejects_code_execution_syntax():
    with pytest.raises(CalculatorError):
        calculate("__import__('os').system('echo nope')")


def test_calculator_rejects_unsupported_operator():
    payload = json.loads(calculator_result_json("2 ** 8"))
    assert "error" in payload
    assert "+, -, * and /" in payload["error"]


def test_calculator_reports_division_by_zero():
    payload = json.loads(calculator_result_json("1 / 0"))
    assert payload == {"error": "Division by zero."}
