"""Declared post-failure actions for grounding issues.

Validation says what is wrong. This module says what a correction is allowed
or required to do next. Finding and repair semantics deliberately stay
separate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Iterable

from src.agent.grounding.figures import Declaration, parse_figures_block, scan_figures


class RepairAction(str, Enum):
    """Post-failure action for one grounding issue."""

    AUTO_REPAIR = "auto_repair"
    PRESERVE_REWRITE = "preserve_rewrite"
    PRESERVE_OPTIONS = "preserve_options"
    DERIVE = "derive"
    REPLACE = "replace"
    RECOVER = "recover"
    DROP = "drop"


@dataclass(frozen=True)
class RepairDirective:
    """One action decision derived from validation metadata."""

    action: RepairAction
    preserve: bool
    exact_ref: str | None = None
    allowed_refs: tuple[str, ...] = ()
    target_scope: str | None = None
    derive_formula: str | None = None
    replacement_value: float | None = None
    replacement_formula: str | None = None


_RECOVERY_REASONS = frozenset({"no_evidence", "symbol_never_handled", "no_symbol"})


def directive_for_issue(issue: dict[str, Any]) -> RepairDirective:
    """Classify an issue without changing the gate's verdict."""

    proven_refs = tuple(
        dict.fromkeys(
            str(item) for item in issue.get("proven_ref_repair_candidates") or []
        )
    )
    exact_ref = issue.get("exact_ref_repair_candidate")
    aggregate_ref = issue.get("aggregate_ref_candidate")
    aggregate_refs = tuple(
        dict.fromkeys(
            str(item) for item in issue.get("aggregate_ref_candidates") or []
        )
    )
    entity = issue.get("entity_ref_symbol")
    reason = str(issue.get("reason") or "")
    derive_formula = issue.get("derive_formula")
    derive_refs = tuple(
        dict.fromkeys(
            str(item) for item in issue.get("derive_operand_refs") or []
        )
    )

    if derive_formula and derive_refs and issue.get("derived_repair_verified") is True:
        return RepairDirective(
            RepairAction.DERIVE,
            preserve=True,
            allowed_refs=derive_refs,
            derive_formula=str(derive_formula),
        )

    replacement_ref = issue.get("replacement_ref_candidate")
    replacement_refs = tuple(
        dict.fromkeys(str(item) for item in issue.get("replacement_refs") or [])
    )
    replacement_value = issue.get("replacement_value")
    replacement_formula = issue.get("replacement_formula")
    if issue.get("replacement_role") == "derived" and issue.get("derived_repair_verified") is not True:
        return RepairDirective(RepairAction.DROP, preserve=False)
    if issue.get("role") == "derived" and issue.get("derived_repair_verified") is not True:
        return RepairDirective(RepairAction.RECOVER if reason in _RECOVERY_REASONS else RepairAction.DROP,
                               preserve=False)
    if (replacement_ref or replacement_refs) and isinstance(replacement_value, (int, float)):
        allowed = (
            replacement_refs
            if replacement_refs
            else (str(replacement_ref),)
        )
        return RepairDirective(
            RepairAction.REPLACE,
            preserve=True,
            exact_ref=str(replacement_ref) if replacement_ref else None,
            allowed_refs=allowed,
            target_scope=(
                f"entity:{issue.get('replacement_entity_symbol')}"
                if issue.get("replacement_entity_symbol")
                else "aggregate"
                if issue.get("replacement_role") == "derived"
                else None
            ),
            replacement_value=float(replacement_value),
            replacement_formula=(
                str(replacement_formula) if replacement_formula else None
            ),
        )

    if len(proven_refs) > 1:
        target_scope = None
        if aggregate_refs and set(aggregate_refs) == set(proven_refs):
            target_scope = "aggregate"
        elif entity:
            target_scope = f"entity:{entity}"
        return RepairDirective(
            RepairAction.PRESERVE_OPTIONS,
            preserve=True,
            allowed_refs=proven_refs,
            target_scope=target_scope,
        )

    if aggregate_ref:
        return RepairDirective(
            RepairAction.PRESERVE_REWRITE,
            preserve=True,
            exact_ref=str(aggregate_ref),
            allowed_refs=(str(aggregate_ref),),
            target_scope="aggregate",
        )
    if entity:
        candidates = list(
            dict.fromkeys(str(item) for item in issue.get("field_ref_candidates") or [])
        )
        entity_ref = exact_ref or (candidates[0] if len(candidates) == 1 else None)
        allowed = (str(entity_ref),) if entity_ref else ()
        return RepairDirective(
            RepairAction.PRESERVE_REWRITE,
            preserve=True,
            exact_ref=str(entity_ref) if entity_ref else None,
            allowed_refs=allowed,
            target_scope=f"entity:{entity}",
        )
    if exact_ref:
        return RepairDirective(
            RepairAction.AUTO_REPAIR,
            preserve=True,
            exact_ref=str(exact_ref),
            allowed_refs=(str(exact_ref),),
        )
    if reason in _RECOVERY_REASONS:
        # Missing evidence is a recovery obligation, not a claim-preservation
        # obligation. The figure is not trusted until recovery succeeds.
        return RepairDirective(RepairAction.RECOVER, preserve=False)
    return RepairDirective(RepairAction.DROP, preserve=False)


@dataclass(frozen=True)
class RequiredFigure:
    """Semantic identity of one rejected-but-repairable measured claim.

    Presence is checked against the corrected draft's validated visible claims
    when available. This keeps the claim bound to its proven exact evidence
    ref across harmless prose rewrites.
    A proven derivation additionally keeps its role, complete operand set and
    supplied machine formula (whitespace may change, arithmetic may not).
    """

    text: str
    value: float
    percent: bool
    digits: str
    allowed_refs: tuple[str, ...] = ()
    require_all_refs: bool = False
    required_role: str | None = None
    required_formula: str | None = None

    @classmethod
    def from_issue(
        cls, issue: dict[str, Any], directive: RepairDirective
    ) -> "RequiredFigure | None":
        text = str(issue.get("value") or "")
        value = issue.get("figure_value")
        percent = issue.get("figure_percent")
        digits = issue.get("figure_digits")
        if directive.action is RepairAction.REPLACE:
            text = str(issue.get("replacement_text") or text)
            value = issue.get("replacement_value")
            digits = issue.get("replacement_digits") or digits
        if not text or not isinstance(value, (int, float)):
            return None
        if not isinstance(percent, bool):
            return None
        return cls(
            text=text,
            value=float(value),
            percent=percent,
            digits=str(digits or ""),
            allowed_refs=directive.allowed_refs or (
                (directive.exact_ref,) if directive.exact_ref else ()
            ),
            required_role=(
                "derived" if directive.action is RepairAction.DERIVE or (
                    directive.action is RepairAction.REPLACE
                    and issue.get("replacement_role") == "derived"
                ) else None
            ),
            required_formula=(
                directive.derive_formula if directive.action is RepairAction.DERIVE
                else directive.replacement_formula
                if issue.get("replacement_role") == "derived" else None
            ),
            require_all_refs=(
                directive.action is RepairAction.DERIVE
                or (
                    directive.action is RepairAction.REPLACE
                    and len(directive.allowed_refs) > 1
                )
            ),
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
        """Whether one validated declaration carries this required claim.

        The gate may accept a provider call id copied without its pipe suffix
        by uniquely canonicalizing the pre-pipe token. The contract runs only
        after grounding validation passes, so it recognizes that same spelling
        when exactly one allowed ref maps to it. Ambiguous aliases stay rejected.
        """

        if self.required_role and declaration.role != self.required_role:
            return False
        if self.required_formula and re.sub(r"\s+", "", declaration.note) != re.sub(
            r"\s+", "", self.required_formula
        ):
            return False
        # The grounding validator permits an observed, unitless contribution
        # declaration to label the *same numeric magnitude* rendered as pp.
        # Keep this exception limited to an exact attribution field reference,
        # never to a generic ratio, money, derived result, or conversion.
        contribution_display = (
            self.percent
            and not declaration.percent
            and declaration.unitless_numeric
            and declaration.role == "observed"
            and bool(self.allowed_refs)
            and any(
                ref.split("::", 1)[-1].endswith(".contribution_pct")
                for ref in self.allowed_refs
                if "::" in ref
            )
        )
        if (
            (declaration.percent != self.percent and not contribution_display)
            or not self._value_matches(declaration.value)
        ):
            return False
        if not self.allowed_refs:
            return True
        refs = {
            part.strip()
            for part in declaration.ref.split(";")
            if part.strip()
        }
        if self.require_all_refs:
            normalized_refs = set(refs)
            for ref in list(refs):
                if "::" not in ref:
                    continue
                call_id, field = ref.split("::", 1)
                if "|" in call_id:
                    continue
                matching = {
                    allowed
                    for allowed in self.allowed_refs
                    if "::" in allowed
                    and allowed.split("::", 1)[0].split("|", 1)[0] == call_id
                    and allowed.split("::", 1)[1] == field
                }
                if len(matching) == 1:
                    normalized_refs.discard(ref)
                    normalized_refs.update(matching)
            return set(self.allowed_refs) == normalized_refs
        if refs.intersection(self.allowed_refs):
            return True

        alias_to_full: dict[str, set[str]] = {}
        for allowed in self.allowed_refs:
            if "::" not in allowed:
                continue
            call_id, field = allowed.split("::", 1)
            if "|" not in call_id:
                continue
            alias = call_id.split("|", 1)[0] + "::" + field
            alias_to_full.setdefault(alias, set()).add(allowed)
        return any(
            ref in alias_to_full and len(alias_to_full[ref]) == 1
            for ref in refs
        )


@dataclass(frozen=True)
class CorrectionContract:
    """Hard invariants for repairable rejected and clean observed claims.

    Rejected claims are mandatory only when their repair directive says
    preserve. Clean observed claims are mandatory only when validation tied a
    visible occurrence to one exact evidence ref; their occurrence and source
    declaration must survive a text-only correction.
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
                and existing.required_role == claim.required_role
                and existing.required_formula == claim.required_formula
                and existing.allowed_refs == claim.allowed_refs
                and abs(existing.value - claim.value)
                <= max(abs(claim.value) * 1e-9, 1e-9)
                for existing in required
            ):
                required.append(claim)
        for item in getattr(validation, "passed_claims", ()):
            try:
                claim = RequiredFigure(
                    text=str(item["text"]), value=float(item["value"]),
                    percent=bool(item["percent"]), digits=str(item.get("digits") or ""),
                    allowed_refs=tuple(item.get("allowed_refs") or ()),
                    required_role="observed",
                )
            except (KeyError, TypeError, ValueError):
                continue
            if not any(
                existing.percent == claim.percent
                and existing.required_role == claim.required_role
                and existing.allowed_refs == claim.allowed_refs
                and abs(existing.value - claim.value) <= max(abs(claim.value) * 1e-9, 1e-9)
                for existing in required
            ):
                required.append(claim)
                exact_ref = claim.allowed_refs[0] if len(claim.allowed_refs) == 1 else None
                directives.append((claim.text, RepairDirective(
                    RepairAction.AUTO_REPAIR, preserve=True, exact_ref=exact_ref,
                    allowed_refs=claim.allowed_refs,
                )))
        return cls(tuple(required), tuple(directives))

    def missing_figures(self, content: str, *, validation: Any = None) -> tuple[str, ...]:
        """Preserve the gate's exact visible claims; retain legacy checks otherwise."""
        # The release path supplies the gate's occurrence-to-declaration
        # associations. An unused declaration cannot preserve a visible claim,
        # and equal values with different refs remain separate obligations.
        passed = tuple(getattr(validation, "passed_claims", ())) if validation is not None else ()
        allowed_by_alias: dict[str, set[str]] = {}
        for required in self.required_figures:
            for allowed in required.allowed_refs:
                if "::" in allowed:
                    call_id, field = allowed.split("::", 1)
                    alias = call_id.split("|", 1)[0] + "::" + field
                    allowed_by_alias.setdefault(alias, set()).add(allowed)
        used_claims: set[int] = set()
        matched: set[int] = set()
        if validation is not None:
            for required_index, required in enumerate(self.required_figures):
                if required.required_role != "observed" or len(required.allowed_refs) != 1:
                    continue
                for claim_index, claim in enumerate(passed):
                    if claim_index in used_claims:
                        continue
                    refs = tuple(claim.get("allowed_refs") or ())
                    if len(refs) != 1 or not required._value_matches(float(claim["value"])):
                        continue
                    if bool(claim["percent"]) != required.percent:
                        continue
                    ref = refs[0]
                    allowed = required.allowed_refs[0]
                    if ref != allowed and not (
                        "::" in ref and "::" in allowed
                        and ref.split("::", 1)[1] == allowed.split("::", 1)[1]
                        and ref.split("::", 1)[0].split("|", 1)[0]
                        == allowed.split("::", 1)[0].split("|", 1)[0]
                        and len(allowed_by_alias.get(
                            ref.split("::", 1)[0].split("|", 1)[0]
                            + "::" + ref.split("::", 1)[1], ()
                        )) == 1
                    ):
                        continue
                    used_claims.add(claim_index)
                    matched.add(required_index)
                    break

        block = parse_figures_block(content)
        figures = scan_figures(content, block)
        used_occurrences: set[int] = set()
        missing: list[str] = []
        for required_index, required in enumerate(self.required_figures):
            if required_index in matched:
                continue
            if validation is not None and required.required_role == "observed" and len(required.allowed_refs) == 1:
                missing.append(required.text)
                continue
            found = False
            for declaration in block.declarations:
                if not required.matches_declaration(declaration):
                    continue
                is_unitless_contribution = (
                    required.percent
                    and not declaration.percent
                    and declaration.unitless_numeric
                    and declaration.role == "observed"
                    and bool(required.allowed_refs)
                    and any(
                        "::" in ref and ref.split("::", 1)[1].endswith(".contribution_pct")
                        for ref in required.allowed_refs
                    )
                )
                for index, figure in enumerate(figures):
                    if index in used_occurrences or figure.shape not in ("measured", "bare"):
                        continue
                    normal = declaration in block.compatible_declarations(
                        figure.value, figure.percent, figure.digits
                    )
                    special = (
                        is_unitless_contribution and figure.percent
                        and required._value_matches(figure.value)
                    )
                    if not normal and not special:
                        continue
                    used_occurrences.add(index)
                    found = True
                    break
                if found:
                    break
            if not found:
                missing.append(required.text)
        return tuple(missing)

    def violation_prompt(self, missing: Iterable[str]) -> str:
        """Focused feedback for a correction that silently dropped content."""

        missing_values = tuple(dict.fromkeys(str(value) for value in missing))
        values = ", ".join(missing_values)
        directives = []
        for value, directive in self.directives:
            if value not in missing_values:
                continue
            fields = [f"{value}: action={directive.action.value}"]
            if directive.exact_ref:
                fields.append("exact_ref=" + directive.exact_ref)
            if directive.allowed_refs:
                fields.append("allowed_refs=" + "; ".join(directive.allowed_refs))
            if directive.target_scope:
                fields.append("target_scope=" + directive.target_scope)
            if directive.derive_formula:
                fields.append("formula=" + directive.derive_formula)
            directives.append(" | ".join(fields))
        requirements = (
            "\nRepair requirements: " + "\n".join(directives)
            if directives else ""
        )
        return (
            "[GROUNDING CORRECTION CONTRACT] The revised draft silently removed "
            "measured claim(s) with deterministic repair directives: "
            f"{values}. Restore those numeric claims and apply their listed repair "
            "directives." + requirements
            + "\nEquivalent numeric formatting is allowed. Do not add unrelated "
            "numeric claims. Return the full revised answer with its figures block."
        )
