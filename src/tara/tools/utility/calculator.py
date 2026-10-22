"""Safe deterministic arithmetic and unit conversion.

`eval` is never used.  Only a tiny AST whitelist and a fixed function table run.
"""
from __future__ import annotations

import ast
import math
from collections.abc import Callable
from typing import Any

from ...core.errors import ToolError
from ..executor import ToolDef

_MAX_CHARS = 200
_MAX_VALUE = 1e12
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs, "round": round, "min": min, "max": max, "sqrt": math.sqrt,
    "floor": math.floor, "ceil": math.ceil, "log": math.log, "log10": math.log10,
    "exp": math.exp, "sin": math.sin, "cos": math.cos, "tan": math.tan,
}
_CONSTANTS = {"pi": math.pi, "e": math.e}

_LENGTH = {
    "mm": 0.001, "millimeter": 0.001, "millimeters": 0.001,
    "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01,
    "m": 1.0, "meter": 1.0, "meters": 1.0,
    "km": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0,
    "in": 0.0254, "inch": 0.0254, "inches": 0.0254,
    "ft": 0.3048, "foot": 0.3048, "feet": 0.3048,
    "yd": 0.9144, "yard": 0.9144, "yards": 0.9144,
    "mi": 1609.344, "mile": 1609.344, "miles": 1609.344,
}
_MASS = {
    "mg": 1e-6, "milligram": 1e-6, "milligrams": 1e-6,
    "g": 1e-3, "gram": 1e-3, "grams": 1e-3,
    "kg": 1.0, "kilogram": 1.0, "kilograms": 1.0,
    "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237, "pounds": 0.45359237,
    "oz": 0.028349523125, "ounce": 0.028349523125, "ounces": 0.028349523125,
}
_TEMP = {"c", "celsius", "f", "fahrenheit", "k", "kelvin"}


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolError("bad_expression", "Only numbers are allowed in a calculation")
    if not math.isfinite(float(value)) or abs(float(value)) > _MAX_VALUE:
        raise ToolError("bad_expression", "The calculation is outside the allowed range")
    return float(value)


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        return _number(node.value)
    if isinstance(node, ast.Name):
        if node.id not in _CONSTANTS:
            raise ToolError("bad_expression", "Unknown name in calculation")
        return _number(_CONSTANTS[node.id])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Add):
            return _number(left + right)
        if isinstance(node.op, ast.Sub):
            return _number(left - right)
        if isinstance(node.op, ast.Mult):
            return _number(left * right)
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ToolError("bad_expression", "Division by zero")
            return _number(left / right)
        if isinstance(node.op, ast.Mod):
            if right == 0:
                raise ToolError("bad_expression", "Division by zero")
            return _number(left % right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > 12:
                raise ToolError("bad_expression", "Exponent is too large")
            return _number(left ** right)
        raise ToolError("bad_expression", "Unsupported operator")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id not in _FUNCTIONS or node.keywords:
            raise ToolError("bad_expression", "Unsupported function")
        args = [_eval(arg) for arg in node.args]
        if len(args) < 1 or len(args) > 3:
            raise ToolError("bad_expression", "Unsupported argument count")
        try:
            return _number(_FUNCTIONS[node.func.id](*args))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ToolError("bad_expression", "The calculation could not be evaluated") from exc
    raise ToolError("bad_expression", "Unsupported expression")


def calculate(expression: str) -> dict[str, object]:
    text = (expression or "").strip()
    if not text or len(text) > _MAX_CHARS:
        raise ToolError("bad_expression", "A short arithmetic expression is required")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ToolError("bad_expression", "The expression is not valid arithmetic") from exc
    value = _eval(tree)
    display = int(value) if value.is_integer() and abs(value) < 1e15 else round(value, 10)
    return {"expression": text, "value": display, "formatted": f"{display:g}" if isinstance(display, float) else str(display)}


def _unit(value: float, from_unit: str, to_unit: str) -> tuple[float, str]:
    a, b = from_unit.strip().lower(), to_unit.strip().lower()
    if a in _TEMP or b in _TEMP:
        if a not in _TEMP or b not in _TEMP:
            raise ToolError("bad_unit", "Temperature units cannot be mixed with other units")
        celsius = value if a in {"c", "celsius"} else (value - 32) * 5 / 9 if a in {"f", "fahrenheit"} else value - 273.15
        out = celsius if b in {"c", "celsius"} else celsius * 9 / 5 + 32 if b in {"f", "fahrenheit"} else celsius + 273.15
        return _number(out), "temperature"
    for table, name in ((_LENGTH, "length"), (_MASS, "mass")):
        if a in table and b in table:
            return _number(value * table[a] / table[b]), name
    raise ToolError("bad_unit", "Those units are not compatible or are not supported")


def convert_units(value: float, from_unit: str, to_unit: str) -> dict[str, object]:
    result, category = _unit(_number(value), from_unit, to_unit)
    return {
        "value": round(result, 10), "from": from_unit.strip(), "to": to_unit.strip(),
        "category": category, "formatted": f"{round(result, 10):g} {to_unit.strip()}",
    }


def calculator_tool_defs() -> tuple[ToolDef, ...]:
    return (
        ToolDef("calculate", {"expression": str}, calculate,
                description="Evaluate safe arithmetic (numbers, + - * / % **, sqrt, abs, round, min, max). "
                            "Never use it for time-zone conversion.",
                timeout_s=3.0, permission="calculation", side_effect=False, category="calculation",
                parameters_schema={"type": "OBJECT", "properties": {
                    "expression": {"type": "STRING", "description": "Arithmetic expression"}},
                    "required": ["expression"]}),
        ToolDef("convert_units", {"value": float, "from_unit": str, "to_unit": str}, convert_units,
                description="Convert length, mass or temperature units deterministically.",
                timeout_s=3.0, permission="calculation", side_effect=False, category="calculation",
                parameters_schema={"type": "OBJECT", "properties": {
                    "value": {"type": "NUMBER", "description": "Numeric value"},
                    "from_unit": {"type": "STRING", "description": "Source unit, e.g. km"},
                    "to_unit": {"type": "STRING", "description": "Target unit, e.g. miles"}},
                    "required": ["value", "from_unit", "to_unit"]}),
    )
