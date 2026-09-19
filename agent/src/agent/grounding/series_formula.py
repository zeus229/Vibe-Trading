"""Structured derivations over observed series: a closed grammar the gate can recompute.

A ``derived`` note may state plain arithmetic over observed numbers (``0.666 × 0.97``), or
a statistic over the daily bars a tool returned. The second form names the series and a
closed set of operations, and the gate evaluates it against its OWN evidence: a claimed
20-session volatility is compared with the volatility the observed closes actually give,
not with the model's arithmetic. No prose is read; a note is a formula or it is not one.

Grammar (``ast``-validated, never ``eval``-ed)::

    expr    := number | series | series[a:b] | expr (+|-|*|/) expr | -expr | (expr)
             | returns(expr) | mean(expr) | sum(expr) | std_sample(expr) | sqrt(expr)
    series  := open | high | low | close | adj_close | volume

* ``close[-21:]`` is the last 21 observed sessions, oldest first; a window longer than the
  observed history is refused rather than silently shortened, and so is a window that reads
  a session two tool results disagree on (a NaN marker from the resolver).
* ``returns(s)`` is the simple return ``s[t] / s[t-1] - 1`` (one element shorter).
* ``std_sample`` is the sample standard deviation (n - 1); ``sqrt`` needs a non-negative
  scalar. A series is never mixed into arithmetic: reduce it with ``mean``/``sum``/``std_sample``.
* Annualizing volatility is spelled ``std_sample(returns(close[-21:])) * sqrt(252)``; the
  ``* 100`` that turns a fraction into a percentage is written out, as for any formula.

Anything outside the grammar (another function, ``**``, a keyword, a second argument, prose)
is not a series formula, so the note falls back to the plain-arithmetic evaluator and is
rejected there exactly as before.
"""

from __future__ import annotations

import ast
import math
import statistics
from typing import Mapping, Sequence

#: The observed daily series a note may name.
SERIES_FIELDS = frozenset({"open", "high", "low", "close", "adj_close", "volume"})

_FUNCTIONS = frozenset({"returns", "mean", "sum", "std_sample", "sqrt"})
_SEPARATORS = ("≈", "≒", "＝", "=", "→", "->")


class SeriesFormulaError(ValueError):
    """A recognised series formula that cannot be evaluated; ``reason`` is the validator's code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _bound(node: ast.AST | None) -> int | None | bool:
    """A slice bound: an integer constant (possibly negated) or None; False when malformed."""
    if node is None:
        return None
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return sign * node.value
    return False


def _in_grammar(node: ast.AST, names: set[str]) -> bool:
    """Whether every node of a parsed formula belongs to the closed grammar."""
    if isinstance(node, ast.Expression):
        return _in_grammar(node.body, names)
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, (ast.USub, ast.UAdd)) and _in_grammar(node.operand, names)
    if isinstance(node, ast.BinOp):
        return isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)) and _in_grammar(
            node.left, names
        ) and _in_grammar(node.right, names)
    if isinstance(node, ast.Name):
        if node.id in SERIES_FIELDS:
            names.add(node.id)
            return True
        return False
    if isinstance(node, ast.Subscript):
        target, window = node.value, node.slice
        return (
            isinstance(target, ast.Name)
            and target.id in SERIES_FIELDS
            and isinstance(window, ast.Slice)
            and window.step is None
            and _bound(window.lower) is not False
            and _bound(window.upper) is not False
            and (names.add(target.id) or True)
        )
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id in _FUNCTIONS
            and len(node.args) == 1
            and not node.keywords
            and _in_grammar(node.args[0], names)
        )
    return False


def parse_series_formula(note: str) -> tuple[ast.Expression, frozenset[str]] | None:
    """Parse a note as a series formula, or None when it is not one.

    The whole note is tried first, then each segment between result separators
    ("std_sample(returns(close[-21:])) * 100 = 1.90"), as the arithmetic evaluator does.

    Args:
        note: The declaration's free-text note.

    Returns:
        ``(tree, series names)`` for the first segment inside the grammar that names at
        least one series, or None.
    """
    pieces = [note]
    for separator in _SEPARATORS:
        pieces = [piece for part in pieces for piece in part.split(separator)]
    for candidate in [note, *pieces]:
        text = (
            candidate.replace("×", "*").replace("✕", "*").replace("÷", "/").replace("−", "-")
            .replace("–", "-").replace("（", "(").replace("）", ")").strip()
        )
        if not text:
            continue
        try:
            tree = ast.parse(text, mode="eval")
        except (SyntaxError, ValueError, MemoryError, RecursionError):
            continue
        names: set[str] = set()
        if _in_grammar(tree, names) and names:
            return tree, frozenset(names)
    return None


def _slice(series: Sequence[float], node: ast.Subscript) -> list[float]:
    lower, upper = _bound(node.slice.lower), _bound(node.slice.upper)  # type: ignore[union-attr]
    if isinstance(lower, int) and lower < 0 and upper is None and len(series) < -lower:
        raise SeriesFormulaError("series_too_short")
    window = list(series[lower:upper])
    if not window:
        raise SeriesFormulaError("series_too_short")
    return _unconflicted(window)


def _unconflicted(values: list[float]) -> list[float]:
    """The values, refused when any session in them is a NaN conflict marker."""
    if any(math.isnan(value) for value in values):
        raise SeriesFormulaError("series_conflict")
    return values


def evaluate_series_formula(
    tree: ast.Expression, series: Mapping[str, Sequence[float]]
) -> float:
    """Evaluate a parsed series formula over observed series.

    Args:
        tree: The tree returned by :func:`parse_series_formula`.
        series: Each named series' observed values, oldest first.

    Returns:
        The formula's value.

    Raises:
        SeriesFormulaError: ``series_too_short`` for a window longer than the history,
            ``formula_not_evaluable`` for a type error, a statistic over too few points,
            a division by zero or a non-finite result.
    """

    def visit(node: ast.AST) -> float | list[float]:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return _unconflicted(list(series[node.id]))
        if isinstance(node, ast.Subscript):
            return _slice(series[node.value.id], node)  # type: ignore[attr-defined]
        if isinstance(node, ast.UnaryOp):
            value = scalar(visit(node.operand))
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = scalar(visit(node.left)), scalar(visit(node.right))
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if right == 0:
                raise SeriesFormulaError("formula_not_evaluable")
            return left / right
        name = node.func.id  # type: ignore[attr-defined]
        argument = visit(node.args[0])  # type: ignore[attr-defined]
        if name == "sqrt":
            value = scalar(argument)
            if value < 0:
                raise SeriesFormulaError("formula_not_evaluable")
            return math.sqrt(value)
        values = vector(argument)
        if name == "returns":
            if len(values) < 2 or any(previous == 0 for previous in values[:-1]):
                raise SeriesFormulaError("formula_not_evaluable")
            return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]
        if name == "mean":
            return statistics.fmean(values)
        if name == "sum":
            return math.fsum(values)
        if len(values) < 2:
            raise SeriesFormulaError("formula_not_evaluable")
        return statistics.stdev(values)

    def scalar(value: float | list[float]) -> float:
        if isinstance(value, list):
            raise SeriesFormulaError("formula_not_evaluable")
        return value

    def vector(value: float | list[float]) -> list[float]:
        if not isinstance(value, list):
            raise SeriesFormulaError("formula_not_evaluable")
        return value

    try:
        result = scalar(visit(tree))
    except (TypeError, ValueError, ZeroDivisionError, OverflowError, statistics.StatisticsError) as exc:
        if isinstance(exc, SeriesFormulaError):
            raise
        raise SeriesFormulaError("formula_not_evaluable") from exc
    if not math.isfinite(result):
        raise SeriesFormulaError("formula_not_evaluable")
    return result
