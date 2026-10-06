from types import SimpleNamespace

from src.agent.grounding.repair_contract import (
    CorrectionContract,
    RepairAction,
    RequiredFigure,
    directive_for_issue,
)


def test_exact_ref_repair_is_preserved():
    directive = directive_for_issue(
        {
            "reason": "field_ref_needs_call_id",
            "exact_ref_repair_candidate": "call_ok::data.metric",
        }
    )
    assert directive.action is RepairAction.AUTO_REPAIR
    assert directive.preserve is True
    assert directive.exact_ref == "call_ok::data.metric"


def test_aggregate_and_entity_scope_are_preserve_rewrites():
    aggregate = directive_for_issue(
        {
            "aggregate_ref_candidate": "call_p::data.concentration.top5_pct",
        }
    )
    entity = directive_for_issue(
        {
            "entity_ref_symbol": "SPY",
            "exact_ref_repair_candidate": "call_s::data.excess_return_pct",
        }
    )

    assert aggregate.action is RepairAction.PRESERVE_REWRITE
    assert aggregate.preserve is True
    assert aggregate.target_scope == "aggregate"
    assert entity.action is RepairAction.PRESERVE_REWRITE
    assert entity.preserve is True
    assert entity.target_scope == "entity:SPY"


def test_unproven_candidate_remains_droppable():
    directive = directive_for_issue(
        {
            "reason": "field_ref_needs_call_id",
            "field_ref_candidates": ["call_fallback::data.metric"],
        }
    )
    assert directive.action is RepairAction.DROP
    assert directive.preserve is False


def test_recover_is_not_a_claim_preservation_obligation():
    directive = directive_for_issue({"reason": "no_evidence"})

    assert directive.action is RepairAction.RECOVER
    assert directive.preserve is False


def test_contract_hard_preserves_only_repairable_rejected_figures():
    validation = SimpleNamespace(
        passed_figures=("3.15%", "12.06%", "3%", "2%"),
        issues=[
            {
                "value": "50.61%",
                "aggregate_ref_candidate": "call_p::data.concentration.top5_pct",
            },
            {
                "value": "99.99%",
                "reason": "value_mismatch",
            },
            {
                "value": "2.93%",
                "reason": "no_evidence",
            },
        ],
    )

    contract = CorrectionContract.from_validation(validation)

    assert [claim.text for claim in contract.required_figures] == ["50.61%"]
    assert contract.missing_figures(
        "Cartera 3.15% YTD 12.06%. Top 5 agregado: 50.61%."
    ) == ()
    assert contract.missing_figures(
        "Cartera 3.15% YTD 12.06%. Se omite el agregado."
    ) == ("50.61%",)


def test_required_figure_matches_equivalent_numeric_formatting():
    claim = RequiredFigure.from_text("0.2250%")
    assert claim is not None

    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "0.2250%",
                "exact_ref_repair_candidate": "call_cer::data.return_pct",
            }
        ],
    )
    contract = CorrectionContract.from_validation(validation)

    assert contract.missing_figures("CER: 0.225%.") == ()


def test_contract_retry_prompt_is_focused():
    validation = SimpleNamespace(
        passed_figures=(),
        issues=[
            {
                "value": "50.61%",
                "aggregate_ref_candidate": "call_p::data.concentration.top5_pct",
            }
        ],
    )
    contract = CorrectionContract.from_validation(validation)
    prompt = contract.violation_prompt(("50.61%",))

    assert "silently removed" in prompt
    assert "50.61%" in prompt
    assert "Restore those numeric claims" in prompt
    assert "Equivalent numeric formatting is allowed" in prompt
    assert "Do not add unrelated numeric claims" in prompt
