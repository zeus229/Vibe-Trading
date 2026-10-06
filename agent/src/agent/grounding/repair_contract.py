"""Declared post-failure actions for grounding issues.

Validation says what is wrong.  This module says what a correction is allowed
or required to do next.  Keeping that distinction explicit prevents the
correction prompt from inferring action semantics from issue wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from src.agent.grounding.figures import parse_figures_block, scan_figures, strip_figures_block


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
        return RepairDirective(RepairAction.RECOVER, preserve=True)
    return RepairDirective(RepairAction.DROP, preserve=False)


@dataclass(frozen=True)
class CorrectionContract:
    """Invariants that a text-only correction must preserve."""

    required_figures: tuple[str, ...]
    directives: tuple[tuple[str, RepairDirective], ...]

    @classmethod
    def from_validation(cls, validation: Any) -> "CorrectionContract":
        required: list[str] = []
        for value in validation.passed_figures:
            text = str(value)
            if text not in required:
                required.append(text)

        directives: list[tuple[str, RepairDirective]] = []
        for issue in validation.issues:
            value = issue.get("value")
            if value is None:
                continue
            directive = directive_for_issue(issue)
            text = str(value)
            directives.append((text, directive))
            if directive.preserve and text not in required:
                required.append(text)

        return cls(tuple(required), tuple(directives))

    def missing_figures(self, content: str) -> tuple[str, ...]:
        """Required measured figures missing from the revised prose."""

        body = strip_figures_block(content)
        block = parse_figures_block(content)
        present = {
            figure.text
            for figure in scan_figures(body, block)
            if figure.shape in {"measured", "bare"}
        }
        return tuple(value for value in self.required_figures if value not in present)

    def violation_prompt(self, missing: Iterable[str]) -> str:
        """Focused feedback for a correction that silently dropped content."""

        values = ", ".join(dict.fromkeys(str(value) for value in missing))
        return (
            "[GROUNDING CORRECTION CONTRACT] The revised draft is grounded, but it "
            "silently removed measured figure(s) that the previous validation required "
            f"to survive: {values}. Restore those exact numeric figures and apply their "
            "listed repair directives. Do not add unrelated numeric claims. Return the "
            "full revised answer with its figures block."
        )
