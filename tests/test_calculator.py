import math

import pytest

from homework_agent.tools.base import ToolError
from homework_agent.tools.calculator import CalculateTool, evaluate, format_result


@pytest.fixture
def calc():
    return CalculateTool()


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("2 + 2", 4),
        ("(-3 + sqrt(9 - 8)) / 2", -1.0),
        ("7 // 2", 3),
        ("2 ** 10", 1024),
        ("log(8, 2)", 3.0),
        ("comb(52, 5)", 2598960),
        ("sin(radians(30))", 0.49999999999999994),
        ("gcd(84, 36)", 12),
    ],
)
def test_evaluates_arithmetic(expression, expected):
    assert evaluate(expression) == pytest.approx(expected)


def test_comparison_lets_the_model_verify_an_identity():
    assert evaluate("2**10 == 1024") is True
    assert evaluate("1/3 > 0.34") is False


def test_fractions_stay_exact(calc):
    assert calc.run(expression="fraction(3, 4) + fraction(1, 6)").startswith("11/12")


def test_constants():
    assert evaluate("pi") == math.pi


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "open('/etc/passwd').read()",
        "(1).__class__",
        "[x for x in range(3)]",
        "lambda: 1",
        "print('hi')",
        "x + 1",
    ],
)
def test_rejects_anything_that_is_not_arithmetic(expression):
    """The evaluator is an allowlist: no imports, attributes, calls, or names."""
    with pytest.raises(ToolError):
        evaluate(expression)


def test_rejects_division_by_zero():
    with pytest.raises(ToolError, match="division by zero"):
        evaluate("1/0")


def test_refuses_to_hang_on_huge_powers():
    with pytest.raises(ToolError, match="exponent too large"):
        evaluate("2 ** 999999")
    with pytest.raises(ToolError, match="too many digits"):
        evaluate("12345678901234567890 ** 9000")


def test_refuses_giant_factorials():
    with pytest.raises(ToolError, match="factorial argument too large"):
        evaluate("factorial(100000)")


def test_empty_expression():
    with pytest.raises(ToolError, match="empty"):
        evaluate("   ")


def test_unknown_function_names_the_alternatives():
    with pytest.raises(ToolError, match="unknown function"):
        evaluate("integrate(2)")


def test_format_result_shows_exact_and_decimal():
    assert format_result(4.0) == "4 (exactly 4)"
    assert format_result(True) == "true"
    assert "0.333" in format_result(1 / 3)
