"""Safe arithmetic and unit conversion; evaluation is never used."""
from __future__ import annotations

import pytest

from tara.errors import ToolError
from tara.orchestration.calculator import calculate, calculator_tool_defs, convert_units


def test_arithmetic_and_known_functions():
    assert calculate("2 + 3 * 4")["value"] == 14
    assert calculate("sqrt(81)")["value"] == 9
    assert calculate("2**10")["value"] == 1024
    assert calculate("min(3, 7, 2)")["value"] == 2
    assert calculate("3.5")["value"] == 3.5


@pytest.mark.parametrize("expression", [
    "__import__('os').system('echo bad')",
    "open('/etc/passwd')",
    "x + 1",
    "1/0",
    "9**99",
    "1 if True else 2",
    "lambda: 1",
])
def test_unsafe_or_unbounded_expressions_are_rejected(expression):
    with pytest.raises(ToolError) as exc:
        calculate(expression)
    assert exc.value.code == "bad_expression"


def test_unit_conversion_and_incompatible_units():
    assert convert_units(3, "km", "miles")["value"] == pytest.approx(1.8641135767)
    assert convert_units(100, "f", "c")["value"] == pytest.approx(37.7777777778)
    assert convert_units(1, "kg", "lb")["value"] == pytest.approx(2.2046226218)
    with pytest.raises(ToolError) as exc:
        convert_units(3, "km", "kg")
    assert exc.value.code == "bad_unit"


def test_calculator_tools_are_read_only():
    defs = calculator_tool_defs()
    assert {d.name for d in defs} == {"calculate", "convert_units"}
    assert all(not d.side_effect and d.cost == "free" for d in defs)
