"""Operand proof for exact generic financial derivations (no arithmetic engine)."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
import math
from typing import Sequence

from src.agent.grounding.evidence import EvidenceRecord


def _instant(value: str) -> str:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else stamp.isoformat()
    except ValueError:
        return value



RefKey = tuple[str, str, str | None]
TermKey = tuple[RefKey, RefKey | None]


def _merge_coefficients(left: dict[TermKey, Fraction], right: dict[TermKey, Fraction], sign: int = 1) -> dict[TermKey, Fraction]:
    """Accumulate by exact source identity, never by numeric evidence value."""
    result = dict(left)
    for ref, coefficient in right.items():
        result[ref] = result.get(ref, Fraction(0)) + sign * coefficient
    return result


@dataclass
class _FinancialForm:
    unit: str
    currency: str | None = None
    coefficients: dict[TermKey, Fraction] = field(default_factory=dict)
    scalar: Fraction | None = None

    def scaled(self, factor: Fraction) -> _FinancialForm:
        return _FinancialForm(self.unit, self.currency,
                              {ref: value * factor for ref, value in self.coefficients.items()})


def validate_operands(tree: ast.Expression, records: Sequence[EvidenceRecord], symbol: str | None, *, money: bool = False, coefficient_proof: dict[TermKey, Fraction] | None = None) -> str | None:
    """Check exact refs, dimensions, temporal identity and every financial leaf.

    Scalars can scale a proven expression, but cannot stand alone as a
    financial numerator or additive term. All declared refs must be consumed;
    equal-valued refs are ambiguous rather than interchangeable.
    """
    if not records or any(r.status != 'observed' or r.value is None or r.identity_scope not in {'entity', 'aggregate'} for r in records):
        return 'no_evidence'
    if symbol and any(r.identity_scope == 'entity' and r.symbol != symbol for r in records):
        return 'operand_entity_conflict'
    # Compare each observation dimension, not whichever date won ingestion's
    # legacy timestamp precedence. Missing snapshot/as_of cannot prove identity.
    for name in ("snapshot_id", "as_of", "date", "trade_date"):
        values = [getattr(r, name) for r in records]
        known = {(_instant(v) if name != "snapshot_id" else v) for v in values if v}
        if len(known) > 1:
            return 'operand_snapshot_conflict'
        if known and name in {"snapshot_id", "as_of"} and any(not v for v in values):
            return 'operand_snapshot_unavailable'
    semantic_keys = {key for r in records for key in (r.temporal_context or {})}
    for key in semantic_keys:
        known = {_instant(r.temporal_context[key]) for r in records
                 if r.temporal_context and key in r.temporal_context}
        if len(known) > 1:
            return 'operand_snapshot_conflict'
    snapshots = {r.snapshot_id for r in records if r.snapshot_id}
    stamps = {_instant(r.timestamp) for r in records if r.timestamp}
    if len(snapshots) > 1 or len(stamps) > 1:
        return 'operand_snapshot_conflict'
    if len({r.call_id for r in records}) > 1 and (
        len(snapshots) != 1 or any(not r.snapshot_id or not r.timestamp for r in records)
    ):
        return 'operand_snapshot_unavailable'
    if any(r.unit not in {'money', 'ratio'} or (r.unit == 'money' and not r.currency) for r in records):
        return 'operand_unit_unavailable'
    used: set[int] = set()

    def reference(record: EvidenceRecord) -> RefKey:
        return record.call_id, record.field, record.scope

    def matching(value: float) -> list[int]:
        return [i for i, r in enumerate(records)
                if math.isclose(float(r.value), value, rel_tol=1e-12, abs_tol=1e-12)]

    def scalar(node: ast.AST) -> _FinancialForm:
        value = getattr(node, "_grounding_value", None)
        if value is None or not math.isfinite(value) or not (abs(value) <= 1 or abs(value) == 100):
            raise ValueError('scalar_subtree_out_of_range')
        return _FinancialForm('scalar', scalar=Fraction(str(value)))

    def leaf(value: float) -> _FinancialForm:
        candidates = matching(value)
        if len(candidates) > 1:
            raise ValueError('operand_ref_ambiguous')
        if candidates:
            i = candidates[0]
            used.add(i)
            record = records[i]
            return _FinancialForm(str(record.unit), record.currency if record.unit == 'money' else None,
                                  {(reference(record), None): Fraction(1)})
        if abs(value) <= 1 or abs(value) == 100:
            return _FinancialForm('scalar', scalar=Fraction(str(value)))
        raise ValueError('financial_operand_not_observed')

    def visit(node: ast.AST) -> _FinancialForm:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return leaf(float(node.value))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            # Preserve a signed observed leaf; otherwise negation is a proven
            # arithmetic operation on its positive observed operand.
            if isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
                if matching(-float(node.operand.value)):
                    return leaf(-float(node.operand.value))
            form = visit(node.operand)
            if form.unit == 'scalar':
                return scalar(node)
            return form.scaled(Fraction(-1 if isinstance(node.op, ast.USub) else 1))
        if not isinstance(node, ast.BinOp):
            raise ValueError('formula_not_evaluable')
        left, right = visit(node.left), visit(node.right)
        if left.unit == right.unit == 'scalar':
            return scalar(node)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left.unit == 'scalar' or right.unit == 'scalar':
                raise ValueError('additive_operand_not_observed')
            denominators = {key[1] for key in (*left.coefficients, *right.coefficients)}
            if (left.unit, left.currency) != (right.unit, right.currency) or len(denominators) > 1:
                raise ValueError('operand_unit_conflict')
            return _FinancialForm(left.unit, left.currency,
                                  _merge_coefficients(left.coefficients, right.coefficients,
                                                      -1 if isinstance(node.op, ast.Sub) else 1))
        if isinstance(node.op, ast.Mult):
            if left.unit == 'scalar':
                return right.scaled(left.scalar)
            if right.unit == 'scalar':
                return left.scaled(right.scalar)
            raise ValueError('operand_unit_conflict')
        if isinstance(node.op, ast.Div):
            if right.unit == 'scalar':
                return left.scaled(Fraction(1) / right.scalar)
            if left.unit == 'scalar' or (left.unit, left.currency) != (right.unit, right.currency):
                raise ValueError('operand_unit_conflict')
            # Normalize only a common, observed single-ref denominator. A sum
            # or a nested financial quotient is not silently treated as scalar.
            if len(right.coefficients) != 1 or any(key[1] is not None for key in left.coefficients):
                raise ValueError('operand_unit_conflict')
            (denominator, nested), divisor = next(iter(right.coefficients.items()))
            if nested is not None:
                raise ValueError('operand_unit_conflict')
            return _FinancialForm('ratio', coefficients={
                (ref, denominator): value / divisor for (ref, _), value in left.coefficients.items()
            })
        raise ValueError('formula_not_evaluable')

    try:
        form = visit(tree.body)
        if money and form.unit != 'money':
            return 'operand_unit_conflict'
        if form.unit == 'scalar' or len(used) != len(records):
            return 'financial_operand_not_observed'
        if coefficient_proof is not None:
            coefficient_proof.update(form.coefficients)
        # Apply the cap to the complete normalized expression, not a branch's
        # maximum: repeated refs add, distinct refs remain distinct, and signed
        # cancellation happens before the effective coefficient is checked.
        if any(abs(value) > 100 for value in form.coefficients.values()):
            return 'scalar_subtree_out_of_range'
    except ZeroDivisionError:
        return 'scalar_subtree_out_of_range'
    except ValueError as exc:
        return str(exc)
    return None
