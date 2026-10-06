"""Declared post-failure actions for grounding issues.

Validation says what is wrong. This module says what a correction is allowed
or required to do next. Finding and repair semantics deliberately stay
separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from src.agent.grounding.figures import (
    Figure,
    parse_figures_block,
    scan_figures,
    strip_figures_block,
)


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
        return RepairDirective(
            RepairAction.PRESERVE_REWRITE,
            preserve=True,
            exact_ref=str(exact_ref) if exact_ref else None,
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
    """Semantic identity of one rejected-but-repairable measured figure."""

    text: str
    value: float
    percent: bool
    currency: bool
    digits: str

    @classmethod
    def from_text(cls, text: str) -> "RequiredFigure | None":
        block = parse_figures_block(text)
        figures = [
            figure
            for figure in scan_figures(strip_figures_block(text, block), block)
            if figure.shape == "measured"
        ]
        if len(figures) != 1:
            return None
        figure = figures[0]
        return cls(
            text=text,
            value=figure.value,
            percent=figure.percent,
            currency=figure.currency,
            digits=figure.digits,
        )

    def matches(self, figure: Figure) -> bool:
        """Whether revised prose carries the same measured claim.

        Formatting may change (for example 0.2250% -> 0.225%). The contract is
        about claim survival, not spelling survival.
        """
        if figure.percent != self.percent or figure.currency != self.currency:
            return False
        target = self.value
        candidate = figure.value
        if abs(candidate - target) <= max(abs(target) * 1e-9, 1e-9):
            return True
        required_digits = self.digits or ""
        if "." not in required_digits:
            return False
        places = len(required_digits.split(".", 1)[1])
        half_unit = 0.5 * 10.0 ** (-places)
        relative = abs(candidate) * 0.005
        return abs(candidate - target) <= max(
            min(relative, half_unit * (1 + 1e-9)), 1e-9
        )


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
            claim = RequiredFigure.from_text(text)
            if claim is not None and not any(
                existing.percent == claim.percent
                and existing.currency == claim.currency
                and abs(existing.value - claim.value)
                <= max(abs(claim.value) * 1e-9, 1e-9)
                for existing in required
            ):
                required.append(claim)
        return cls(tuple(required), tuple(directives))

    def missing_figures(self, content: str) -> tuple[str, ...]:
        """Required repairable claims missing semantically from revised prose."""

        block = parse_figures_block(content)
        body = strip_figures_block(content, block)
        body_block = parse_figures_block(body)
        present = [
            figure
            for figure in scan_figures(body, body_block)
            if figure.shape == "measured"
        ]
        return tuple(
            required.text
            for required in self.required_figures
            if not any(required.matches(figure) for figure in present)
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
