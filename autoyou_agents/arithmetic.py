# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Exact results for plain arithmetic questions.

Language models get operator precedence and long arithmetic wrong: "What is
37x52-3/3" came back as 1921/3 instead of 1923 (owner report 2026-11-03). When
a whole message is an arithmetic question, the root model is handed the exact
result for that model call, computed here with exact fractions. Nothing is
evaluated with ``eval``: the expression is parsed with ``ast`` and only numbers,
+ - * / ** and parentheses are accepted, with bounded sizes and exponents.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import ast
import re
from fractions import Fraction
from typing import Any, Dict, Optional

_MAX_EXPRESSION_CHARS = 120
_MAX_EXPONENT = 64
_MAX_RESULT_DIGITS = 60
_DECIMAL_PLACES = 10

_VOICE_PREFIX = re.compile(r"^\s*\[voice transcript\]\s*", re.IGNORECASE)
# English and Spanish question openings (owner, 2026-11-03: English + Spanish
# for now). The `calculate` tool below needs no wording at all.
_QUESTION_PREFIX = re.compile(
    r"^\s*(?:(?:please\s+)?(?:calculate|compute|evaluate|solve|work\s+out)\b\s*:?|"
    r"what\s+is|what's|whats|how\s+much\s+is|"
    r"cu[áa]nto\s+(?:es|son|da)|qu[ée]\s+es|(?:calcula|calcular|calcule|resuelve|resolver|eval[úu]a)\b\s*:?)\s*",
    re.IGNORECASE,
)
_POLITENESS = re.compile(r"\b(?:please|por\s+favor)\b", re.IGNORECASE)
_TRAILING = re.compile(r"\s*(?:=\s*\??|\?+|\.)\s*$")
# Longer phrases first: "multiplicado por" before "por", "dividido entre" before "entre".
_WORD_OPERATORS = (
    (re.compile(r"\bto\s+the\s+power\s+of\b", re.IGNORECASE), "^"),
    (re.compile(r"\b(?:elevado\s+a(?:\s+la)?|a\s+la\s+potencia(?:\s+de)?)\b", re.IGNORECASE), "^"),
    (re.compile(r"\b(?:multiplied\s+by|times|multiplicado\s+por)\b", re.IGNORECASE), "*"),
    (re.compile(r"\b(?:divided\s+by|dividido\s+(?:entre|por))\b", re.IGNORECASE), "/"),
    (re.compile(r"\bpor\b", re.IGNORECASE), "*"),
    (re.compile(r"\bentre\b", re.IGNORECASE), "/"),
    (re.compile(r"\b(?:plus|m[áa]s)\b", re.IGNORECASE), "+"),
    (re.compile(r"\b(?:minus|menos)\b", re.IGNORECASE), "-"),
)
_THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_LETTER_TIMES = re.compile(r"(?<=[\d)])\s*[xX]\s*(?=[\d(.])")
_ALLOWED = re.compile(r"^[\d\s.+\-*/()^]+$")
_OPERATOR = re.compile(r"\*\*|[+\-*/]")


def _normalize(expression: str) -> Optional[str]:
    text = expression
    for pattern, symbol in _WORD_OPERATORS:
        text = pattern.sub(f" {symbol} ", text)
    text = (
        text.replace("×", "*").replace("÷", "/").replace("−", "-")
        .replace("–", "-").replace("—", "-").replace("⋅", "*").replace("·", "*")
    )
    text = _LETTER_TIMES.sub("*", text)
    text = _THOUSANDS.sub(lambda match: match.group(0).replace(",", ""), text)
    if not _ALLOWED.match(text):
        return None
    return text.replace("^", "**")


def _evaluate(node: ast.AST, source: str) -> Fraction:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, source)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        literal = ast.get_source_segment(source, node) or str(node.value)
        return Fraction(literal)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _evaluate(node.operand, source)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, source)
        right = _evaluate(node.right, source)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ZeroDivisionError
            return left / right
        if isinstance(node.op, ast.Pow):
            if right.denominator != 1 or abs(right) > _MAX_EXPONENT or (left == 0 and right < 0):
                raise ValueError("unsupported power")
            return left ** int(right)
    raise ValueError("unsupported expression")


def _has_binary_operation(normalized: str) -> bool:
    """True when the expression combines two values ("-5" alone does not)."""
    try:
        tree = ast.parse(normalized.strip(), mode="eval")
    except SyntaxError:
        return False
    return any(isinstance(node, ast.BinOp) for node in ast.walk(tree))


def arithmetic_expression(message: str) -> Optional[str]:
    """The arithmetic expression a message asks for, or None.

    A message qualifies only when all of it is the question: an optional
    "what is" / "calculate" opening, the expression, and an optional "?" or "=".
    Without an opening or a trailing "=", the expression must clearly be
    arithmetic (a *, x, / or ^ among two or more operations, or *, x or ^ on
    its own), so a phone number ("555-1234") or a date ("2026-11-03") never
    qualifies.
    """
    text = _VOICE_PREFIX.sub("", str(message or "")).strip()
    # "12 por 7, por favor" leaves a comma at the edge once the courtesy goes.
    text = " ".join(_POLITENESS.sub(" ", text).split()).lstrip("¿¡").strip(" ,")
    if not text or len(text) > _MAX_EXPRESSION_CHARS + 40:
        return None
    asked = bool(_QUESTION_PREFIX.match(text))
    text = _QUESTION_PREFIX.sub("", text, count=1)
    trailing_equals = bool(re.search(r"=\s*\??\s*$", text))
    text = _TRAILING.sub("", text).strip()
    if not text or len(text) > _MAX_EXPRESSION_CHARS:
        return None
    normalized = _normalize(text)
    if normalized is None or not re.search(r"\d", normalized) or not _has_binary_operation(normalized):
        return None
    operators = _OPERATOR.findall(normalized.replace(" ", ""))
    if not (asked or trailing_equals):
        strong = any(op in ("*", "/", "**") for op in operators) or "(" in normalized
        single_strong = len(operators) == 1 and operators[0] in ("*", "**")
        if not ((len(operators) >= 2 and strong) or single_strong):
            return None
    return text


def evaluate_arithmetic(expression: str) -> Optional[Fraction]:
    """The exact value of an arithmetic expression, or None when it is not one."""
    normalized = _normalize(str(expression or ""))
    if normalized is None:
        return None
    try:
        tree = ast.parse(normalized.strip(), mode="eval")
        value = _evaluate(tree, normalized.strip())
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, RecursionError):
        return None
    if len(str(abs(value.numerator))) > _MAX_RESULT_DIGITS or len(str(value.denominator)) > _MAX_RESULT_DIGITS:
        return None
    return value


def format_value(value: Fraction) -> str:
    """An exact integer, or a decimal with the exact fraction beside it."""
    if value.denominator == 1:
        return str(value.numerator)
    scaled = round(value * 10**_DECIMAL_PLACES)
    sign = "-" if scaled < 0 else ""
    whole, part = divmod(abs(scaled), 10**_DECIMAL_PLACES)
    decimal = f"{sign}{whole}.{part:0{_DECIMAL_PLACES}d}".rstrip("0").rstrip(".")
    return f"{decimal} (exactly {value.numerator}/{value.denominator})"


def calculate(expression: str) -> Dict[str, Any]:
    """Work out an arithmetic expression exactly. Use it instead of doing arithmetic yourself.

    Accepts numbers, + - * / ^ (also x, ×, ÷) and parentheses, for example
    "37*52-3/3" or "(2+3)^2". It uses the usual order of operations.

    Args:
        expression: The arithmetic to work out, written with numbers and operators.
    """
    value = evaluate_arithmetic(expression)
    if value is None:
        return {
            "status": "error",
            "message": "That is not an arithmetic expression I can work out exactly. Use numbers, + - * / ^ and parentheses.",
        }
    return {"status": "success", "expression": str(expression or "").strip(), "result": format_value(value)}


def calculator_note(message: str) -> str:
    """The note handed to the model for an arithmetic question, or an empty string."""
    expression = arithmetic_expression(message)
    if not expression:
        return ""
    value = evaluate_arithmetic(expression)
    if value is None:
        return ""
    return (
        f"[Calculator] {expression} = {format_value(value)}. This was computed exactly with the usual "
        "order of operations (powers, then x and /, then + and -). Give the user this result; show the "
        "steps in that order if useful, and never a different result."
    )
