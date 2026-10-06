"""Declared post-failure actions for grounding issues.

Validation says what is wrong. This module says what a correction is allowed
or required to do next. Finding and repair semantics deliberately stay
separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from src.agent.grounding.figures import Declaration, parse_figures_block


class RepairAction(str, Enum):
    """Post-failure action for one grounding issue."""

    AUTO_REPAIR = "auto_repair"
    PRESERVE_REWRITE = "preserve_rewrite"
    RECOVER = "recover"
    DROP = "drop"


@dataclass(frozen=True)
class RepairDirective:
    """One action decision derived from validation metadata."""

    action: RepairAction
    preserve: bool
    exact_ref: str | None = None
    target_scope: str | None = None


_RECOVERY_REASONS = frozenset({"no_evidence", "symbol_never_handled", "no_symbol"})


def directive_for_issue(issue: dict[str, Any]) -> RepairDirective:
    """Classify an issue without changing the gate's verdict."""

    exact_ref = issue.get("exact_ref_repair_candidate")
    aggregate_ref = issue.get("aggregate_ref_candidate")
    entity = issue.get("entity_ref_symbol")
    reason = str(issue.get("reason") or "")

    if aggregate_ref:
        return RepairDirective(
            RepairAction.PRESERVE_REWRITE,
            preserve=True,
            exact_ref=str(aggregate_ref),
            target_scope="aggregate",
        )
    if entity:
        candidates = list(
            dict.fromkeys(str(item) for item in issue.get("field_ref_candidates") or [])
        )
        entity_ref = exact_ref or (candidates[0] if len(candidates) == 1 else None)
        return RepairDirective(
            RepairAction.PRESERVE_REWRITE,
            preserve=True,
            exact_ref=str(entity_ref) if entity_ref else None,
            target_scope=f"entity:{entity}",
        )
    if exact_ref:
        return RepairDirective(
            RepairAction.AUTO_REPAIR,
            preserve=True,
            exact_ref=str(exact_ref),
        )
    if reason in _RECOVERY_REASONS:
        # Missing evidence is a recovery obligation, not a claim-preservation
        # obligation. The figure is not trusted until recovery succeeds.
        return RepairDirective(RepairAction.RECOVER, preserve=False)
    return RepairDirective(RepairAction.DROP, preserve=False)


@dataclass(frozen=True)
class RequiredFigure:
    """Semantic identity of one rejected-but-repairable measured claim.

    Presence is checked against the corrected draft's validated figures block,
    not against presentation context in prose. This makes the contract stable
    across harmless rewrites such as currency labels moving around the number,
    while still binding the claim to its proven exact evidence ref when known.
    """

    text: str
    value: float
    percent: bool
    digits: str
    exact_ref: str | None = None

    @classmethod
    def from_issue(
        cls, issue: dict[str, Any], directive: RepairDirective
    ) -> "RequiredFigure | None":
        text = str(issue.get("value") or "")
        value = issue.get("figure_value")
        percent = issue.get("figure_percent")
        digits = issue.get("figure_digits")
        if not text or not isinstance(value, (int, float)):
            return None
        if not isinstance(percent, bool):
            return None
        return cls(
            text=text,
            value=float(value),
            percent=percent,
            digits=str(digits or ""),
            exact_ref=directive.exact_ref,
        )

    def _value_matches(self, value: float) -> bool:
        target = self.value
        if abs(value - target) <= max(abs(target) * 1e-9, 1e-9):
            return True
        required_digits = self.digits or ""
        if "." not in required_digits:
            return False
        places = len(required_digits.split(".", 1)[1])
        half_unit = 0.5 * 10.0 ** (-places)
        relative = abs(value) * 0.005
        return abs(value - target) <= max(
            min(relative, half_unit * (1 + 1e-9)), 1e-9
        )

    def matches_declaration(self, declaration: Declaration) -> bool:
        """Whether one validated declaration carries this required claim."""

        if declaration.percent != self.percent or not self._value_matches(declaration.value):
            return False
        if not self.exact_ref:
            return True
        refs = {
            part.strip()
            for part in declaration.ref.split(";")
            if part.strip()
        }
        return self.exact_ref in refs


@dataclass(frozen=True)
class CorrectionContract:
    """Hard invariants for rejected figures with deterministic repairs.

    Clean figures from #1702 remain soft prompt guidance. They are not hard
    contract members because a correction may legitimately reformat or omit a
    non-required clean detail. Only rejected figures whose repair directive
    explicitly says preserve are mandatory here.
    """

    required_figures: tuple[RequiredFigure, ...]
    directives: tuple[tuple[str, RepairDirective], ...]

    @classmethod
    def from_validation(cls, validation: Any) -> "CorrectionContract":
        required: list[RequiredFigure] = []
        directives: list[tuple[str, RepairDirective]] = []
        for issue in validation.issues:
            value = issue.get("value")
            if value is None:
                continue
            directive = directive_for_issue(issue)
            text = str(value)
            directives.append((text, directive))
            if not directive.preserve:
                continue
            claim = RequiredFigure.from_issue(issue, directive)
            if claim is not None and not any(
                existing.percent == claim.percent
                and existing.exact_ref == claim.exact_ref
                and abs(existing.value - claim.value)
                <= max(abs(claim.value) * 1e-9, 1e-9)
                for existing in required
            ):
                required.append(claim)
        return cls(tuple(required), tuple(directives))

    def missing_figures(self, content: str) -> tuple[str, ...]:
        """Required repairable claims absent from the validated figures block."""

        block = parse_figures_block(content)
        return tuple(
            required.text
            for required in self.required_figures
            if not any(
                required.matches_declaration(declaration)
                for declaration in block.declarations
            )
        )

    def violation_prompt(self, missing: Iterable[str]) -> str:
        """Focused feedback for a correction that silently dropped content."""

        values = ", ".join(dict.fromkeys(str(value) for value in missing))
        return (
            "[GROUNDING CORRECTION CONTRACT] The revised draft is grounded, but it "
            "silently removed measured claim(s) with deterministic repair directives: "
            f"{values}. Restore those numeric claims and apply their listed repair "
            "directives. Equivalent numeric formatting is allowed. Do not add unrelated "
            "numeric claims. Return the full revised answer with its figures block."
        )
