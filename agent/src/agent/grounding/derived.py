"""Operand proof for exact generic financial derivations (no arithmetic engine)."""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import math
from typing import Sequence

from src.agent.grounding.evidence import EvidenceRecord


def _instant(value: str) -> str:
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo else stamp.isoformat()
    except ValueError:
        return value


def validate_operands(tree: ast.Expression, records: Sequence[EvidenceRecord], symbol: str | None, *, money: bool = False) -> str | None:
    """Check exact refs, dimensions, temporal identity and every financial leaf.

    Scalars can scale a proven expression, but cannot stand alone as a
    financial numerator or additive term. All declared refs must be consumed;
    equal-valued refs are ambiguous rather than interchangeable.
    """
    if not records or any(r.status != 'observed' or r.value is None or r.identity_scope not in {'entity', 'aggregate'} for r in records):
        return 'no_evidence'
    if symbol and any(r.identity_scope == 'entity' and r.symbol != symbol for r in records):
        return 'operand_entity_conflict'
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

    def visit(node: ast.AST) -> tuple[str, str | None]:
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            # Signed financial leaves retain their sign for evidence matching.
            if isinstance(node.operand, ast.Constant):
                value = node.operand.value
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return leaf(-value if isinstance(node.op, ast.USub) else value)
            return visit(node.operand)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return leaf(float(node.value))
        if not isinstance(node, ast.BinOp):
            raise ValueError('formula_not_evaluable')
        left, right = visit(node.left), visit(node.right)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left[0] == 'scalar' or right[0] == 'scalar':
                raise ValueError('additive_operand_not_observed')
            if left != right:
                raise ValueError('operand_unit_conflict')
            return left
        if isinstance(node.op, ast.Mult):
            if left[0] == 'scalar':
                return right
            if right[0] == 'scalar':
                return left
            raise ValueError('operand_unit_conflict')
        if isinstance(node.op, ast.Div):
            if right[0] == 'scalar':
                return left
            if left[0] != 'scalar' and left == right:
                return 'ratio', None
            raise ValueError('operand_unit_conflict')
        raise ValueError('formula_not_evaluable')

    def leaf(value: float) -> tuple[str, str | None]:
        candidates = [i for i, r in enumerate(records) if math.isclose(float(r.value), value, rel_tol=1e-12, abs_tol=1e-12)]
        if len(candidates) > 1:
            raise ValueError('operand_ref_ambiguous')
        if candidates:
            i = candidates[0]
            used.add(i)
            r = records[i]
            return str(r.unit), r.currency if r.unit == 'money' else None
        if abs(value) <= 1 or abs(value) == 100:
            return 'scalar', None
        raise ValueError('financial_operand_not_observed')

    try:
        dimension = visit(tree.body)
        if money and dimension[0] != 'money':
            return 'operand_unit_conflict'
        if dimension[0] == 'scalar' or len(used) != len(records):
            return 'financial_operand_not_observed'
    except ValueError as exc:
        return str(exc)
    return None
