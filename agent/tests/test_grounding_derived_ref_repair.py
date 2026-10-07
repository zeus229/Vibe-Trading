"""Complete, fail-closed repairs preserve derived intent and exact operands."""
import json

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.policies import _evaluate_formula
from src.agent.grounding.repair_contract import CorrectionContract, RepairAction, directive_for_issue

pytestmark = pytest.mark.unit
CALL = "call_ABC"
A = "context.holdings[0].market_value_ars"
B = "context.holdings[1].market_value_ars"
TOTAL = "context.totals.market_value_ars"
WEIGHT = "context.holdings[0].weight_portfolio"


def payload():
    return {"context": {"snapshot_id": "XYZ", "as_of": "2026-10-07T03:31:01Z",
                        "totals": {"market_value_ars": 222105473.58200002},
                        "holdings": [
                            {"entity_id": "ENTITY_A", "currency": "ARS",
                             "market_value_ars": 6615045, "weight_portfolio": 0.02978335},
                            {"entity_id": "ENTITY_B", "currency": "ARS",
                             "market_value_ars": 1384425, "weight_portfolio": 0.00623319}]}}


def gate(tmp_path, data=None):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Calculate scenarios for ENTITY_A and ENTITY_B")
    ingest(ledger, data or payload())
    return ledger


def ingest(ledger, data, call=CALL):
    ledger.ingest_tool_result(tool_name="generic_snapshot", arguments={},
                              result=json.dumps(data), call_id=call, success=True)


def draft(value, formula, paths, *, entity="ENTITY_A", percent=False, declared_percent=None, prefix="XYZ"):
    suffix = "%" if percent else ""
    declared_suffix = "%" if (percent if declared_percent is None else declared_percent) else ""
    refs = "; ".join(prefix + "::" + path for path in paths)
    return (f"{entity}: {value}{suffix} percentage points.\n\n```figures\n"
            f"{value}{declared_suffix} | derived | {formula} | {refs}\n```")


def repair(ledger, text):
    validation = ledger.validate_final_answer(text)
    assert not validation.valid
    issues = [issue for issue in validation.issues if issue.get("role") == "derived"]
    assert len(issues) == 1, validation.issues
    return validation, issues[0]


def corrected(issue):
    return (f"Result: {issue['value']}.\n```figures\n"
            f"{issue['value'].replace(',', '.')} | derived | {issue['derive_formula']} | "
            + "; ".join(issue["derive_operand_refs"]) + "\n```")


@pytest.mark.parametrize("factor,value", [("0.25", "0.74458375"), ("0.50", "1.4891675")])
@pytest.mark.parametrize("mismatched_shape", [False, True])
def test_fraction_scenarios_have_complete_preserved_repairs(tmp_path, factor, value, mismatched_shape):
    ledger = gate(tmp_path)
    validation, issue = repair(ledger, draft(value, f"6615045 * {factor} / 222105473.58200002 * 100",
                                           [A, TOTAL], declared_percent=mismatched_shape))
    directive = directive_for_issue(issue)
    assert directive.action is RepairAction.DERIVE
    assert directive.preserve
    assert issue["derive_operand_refs"] == [CALL + "::" + A, CALL + "::" + TOTAL]
    revised = corrected(issue)
    assert ledger.revalidate(revised).valid
    assert CorrectionContract.from_validation(validation).missing_figures(revised) == ()
    assert not any(record.value in {1653761.25, 3307522.5} for record in ledger._evidence)


@pytest.mark.parametrize("percent,shape", [(True, True), (False, True), (False, False)])
def test_equal_observation_does_not_downgrade_derived_intent(tmp_path, percent, shape):
    ledger = gate(tmp_path)
    validation, issue = repair(ledger, draft("0.623319", "1384425 / 222105473.58200002 * 100",
                                           [B, TOTAL], entity="ENTITY_B", percent=percent,
                                           declared_percent=shape))
    assert directive_for_issue(issue).action is RepairAction.DERIVE
    contract = CorrectionContract.from_validation(validation)
    revised = corrected(issue)
    assert ledger.revalidate(revised).valid
    assert not contract.missing_figures(revised)
    downgraded = revised.replace("| derived |", "| observed |").replace(
        "; ".join(issue["derive_operand_refs"]), CALL + "::context.holdings[1].weight_portfolio")
    assert ledger.revalidate(downgraded).valid  # Numerical validity is insufficient.
    assert contract.missing_figures(downgraded) == (issue["value"],)


@pytest.mark.parametrize("factor,value", [("0.25", "0.74458375"), ("0.50", "1.4891675")])
def test_single_financial_ref_and_percent_operands_can_be_repaired(tmp_path, factor, value):
    ledger = gate(tmp_path)
    _, issue = repair(ledger, draft(value, f"2.978335% * {float(factor) * 100:g}%; scenario",
                                   [WEIGHT], declared_percent=True))
    assert issue["derive_operand_refs"] == [CALL + "::" + WEIGHT]
    assert "%" not in issue["derive_formula"]
    evaluated = _evaluate_formula(issue["derive_formula"])
    assert evaluated is not None and evaluated[0] == pytest.approx(float(value))
    assert directive_for_issue(issue).preserve
    assert ledger.revalidate(corrected(issue)).valid


@pytest.mark.parametrize("change", ["drop", "role", "ref", "formula", "value"])
def test_correction_contract_requires_complete_semantic_claim(tmp_path, change):
    ledger = gate(tmp_path)
    validation, issue = repair(ledger, draft("0.74458375", "6615045 * 0.25 / 222105473.58200002 * 100", [A, TOTAL]))
    revised = corrected(issue)
    if change == "drop":
        revised = "Scenario omitted."
    elif change == "role":
        revised = revised.replace("| derived |", "| observed |")
    elif change == "ref":
        revised = revised.replace("; " + CALL + "::" + TOTAL, "")
    elif change == "formula":
        revised = revised.replace("0.25", "0.50")
    else:
        revised = revised.replace("0.74458375", "0.74450000")
    assert CorrectionContract.from_validation(validation).missing_figures(revised) == (issue["value"],)


def test_formula_changes_rejected_even_when_result_and_refs_are_equal(tmp_path):
    ledger = gate(tmp_path)
    validation, issue = repair(ledger, draft("0.74458375", "6615045 * 0.25 / 222105473.58200002 * 100", [A, TOTAL]))
    revised = corrected(issue).replace(issue["derive_formula"], "6615045 / 222105473.58200002 * 25")
    assert ledger.revalidate(revised).valid
    assert CorrectionContract.from_validation(validation).missing_figures(revised)


@pytest.mark.parametrize("mutation", ["missing", "wrong_entity", "invented", "invalid_formula", "out_of_range", "bad_path", "wrong_existing_call"])
@pytest.mark.parametrize("shape", [False, True])
def test_incomplete_repairs_never_get_preserve_metadata(tmp_path, mutation, shape):
    ledger = gate(tmp_path)
    paths, formula, prefix = [A, TOTAL], "6615045 * 0.25 / 222105473.58200002 * 100", "XYZ"
    if mutation == "missing":
        paths = [A]
    elif mutation == "wrong_entity":
        paths, formula = [B, TOTAL], "1384425 * 0.25 / 222105473.58200002 * 100"
    elif mutation == "invented":
        formula = formula.replace("6615045", "1653761.25")
    elif mutation == "invalid_formula":
        formula = "not arithmetic"
    elif mutation == "out_of_range":
        formula = "6615045 * 10000 / 222105473.58200002"
    elif mutation == "bad_path":
        paths = [A, "context.missing"]
    else:
        ingest(ledger, {"context": {"totals": {"market_value_ars": 1}}}, "call_wrong")
        prefix = "call_wrong"
    validation = ledger.validate_final_answer(draft("0.74458375", formula, paths, declared_percent=shape, prefix=prefix))
    assert not validation.valid
    for issue in validation.issues:
        assert "derive_operand_refs" not in issue
        assert not directive_for_issue(issue).preserve


@pytest.mark.parametrize("mutation", ["ambiguous", "snapshot", "as_of", "date", "trade_date", "currency", "unit"])
def test_cross_call_repairs_are_fail_closed(tmp_path, mutation):
    data = payload()
    if mutation in {"date", "trade_date"}:
        data["context"][mutation] = "2026-10-07"
    ledger = gate(tmp_path, data)
    other = payload()
    if mutation == "snapshot":
        other["context"]["snapshot_id"] = "other"
    elif mutation in {"as_of", "date", "trade_date"}:
        other["context"]["as_of"] = "2026-10-08T03:31:01Z"
        if mutation != "as_of":
            other["context"][mutation] = "2026-10-07"
    elif mutation == "currency":
        other["context"]["totals"]["currency"] = "USD"
    elif mutation == "unit":
        other["context"]["totals"]["weight_total"] = other["context"]["totals"].pop("market_value_ars")
    ingest(ledger, other, "call_OTHER")
    total_ref = "XYZ::" + TOTAL if mutation == "ambiguous" else "call_OTHER::" + (
        "context.totals.weight_total" if mutation == "unit" else TOTAL)
    text = draft("0.74458375", "6615045 * 0.25 / 222105473.58200002 * 100", [A, TOTAL], prefix=CALL)
    text = text.replace(CALL + "::" + TOTAL, total_ref)
    validation = ledger.validate_final_answer(text)
    assert not validation.valid
    assert not any(issue.get("derive_formula") for issue in validation.issues)
    assert not any(directive_for_issue(issue).preserve for issue in validation.issues)


def test_wrong_prefix_is_generic_not_snapshot_specific(tmp_path):
    ledger = gate(tmp_path)
    _, issue = repair(ledger, draft("0.74458375", "6615045 * 0.25 / 222105473.58200002 * 100",
                                   [A, TOTAL], prefix="arbitrary_label"))
    assert issue["derive_operand_refs"] == [CALL + "::" + A, CALL + "::" + TOTAL]


def test_equal_valued_wrong_entity_does_not_become_repair(tmp_path):
    data = payload()
    data["context"]["holdings"][1]["market_value_ars"] = 6615045
    ledger = gate(tmp_path, data)
    _, issue = repair(ledger, draft("0.74458375", "6615045 * 0.25 / 222105473.58200002 * 100", [B, TOTAL]))
    assert not directive_for_issue(issue).preserve


def test_ambiguous_derived_declarations_not_replaced_by_equal_observed_hint(tmp_path):
    ledger = gate(tmp_path)
    text = draft("0.623319", "1384425 / 222105473.58200002 * 100", [B, TOTAL],
                 entity="ENTITY_B", declared_percent=True)
    line = text.splitlines()[-2]
    text = text.replace(line, line + "\n" + line.replace("XYZ", "another_label"))
    _, issue = repair(ledger, text)
    assert not directive_for_issue(issue).preserve
    assert "entity_ref_symbol" not in issue


def test_equal_valued_different_refs_remain_ambiguous(tmp_path):
    data = payload()
    data["context"]["holdings"][1]["market_value_ars"] = 6615045
    ledger = gate(tmp_path, data)
    text = draft("5.956670", "(6615045 + 6615045) / 222105473.58200002 * 100",
                 [A, B, TOTAL], entity="Total", declared_percent=True)
    _, issue = repair(ledger, text)
    assert not directive_for_issue(issue).preserve


def test_changed_result_cannot_get_verified_repair(tmp_path):
    ledger = gate(tmp_path)
    _, issue = repair(ledger, draft("9.74458375", "6615045 * 0.25 / 222105473.58200002 * 100", [A, TOTAL]))
    assert not directive_for_issue(issue).preserve
