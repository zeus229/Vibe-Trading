"""Repair proves structural operand coverage before promising preservation."""
import json

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.repair_contract import CorrectionContract, directive_for_issue

pytestmark = pytest.mark.unit
A = "data.rows[0].market_value"
B = "data.rows[1].market_value"
TOTAL = "data.totals.market_value"


def payload(total=10000):
    return {"identifier": "XYZ", "data": {
        "snapshot_id": "snapshot-a", "as_of": "2026-10-07T00:00:00Z",
        "totals": {"currency": "USD", "market_value": total},
        "rows": [{"entity_id": "ENTITY_A", "currency": "USD", "market_value": 125},
                 {"entity_id": "ENTITY_B", "currency": "USD", "market_value": 250}],
    }}


def ledger(tmp_path, data):
    gate = GroundingLedger(run_dir=tmp_path, user_message="ENTITY_A and ENTITY_B scenarios")
    ingest(gate, data)
    return gate


def ingest(gate, data, call="call_ABC"):
    gate.ingest_tool_result(tool_name="read_measurements", arguments={},
                           result=json.dumps(data), call_id=call, success=True)


def validate(gate, formula, value, paths, entity="ENTITY_A"):
    refs = "; ".join(path if "::" in path else "XYZ::" + path for path in paths)
    text = (f"{entity} change: {value:.6f} pp.\n```figures\n"
            f"{value:.6f}% | derived | {formula} | {refs}\n```")
    result = gate.validate_final_answer(text)
    assert not result.valid
    issues = [issue for issue in result.issues if issue.get("role") == "derived"]
    assert len(issues) == 1, result.issues
    return result, issues[0]


def corrected(issue):
    return (f"Change {issue['value']} pp.\n```figures\n"
            f"{issue['value']} | derived | {issue['derive_formula']} | "
            + "; ".join(issue["derive_operand_refs"]) + "\n```")


@pytest.mark.parametrize("total", [2000, 5000, 10000])
@pytest.mark.parametrize("factor", [1, 0.25, 0.50])
def test_complete_entity_and_total_repairs_are_preserved(tmp_path, total, factor):
    gate = ledger(tmp_path, payload(total))
    result, issue = validate(gate, f"125 * {factor} / {total} * 100",
                             125 * factor / total * 100, [A, TOTAL])
    assert directive_for_issue(issue).preserve
    assert issue["derive_operand_refs"] == ["call_ABC::" + A, "call_ABC::" + TOTAL]
    revised = corrected(issue)
    assert gate.revalidate(revised).valid
    assert not CorrectionContract.from_validation(result).missing_figures(revised)


@pytest.mark.parametrize("total", [2000, 5000, 10000])
@pytest.mark.parametrize("paths", [[A], [TOTAL]])
def test_missing_numerator_or_denominator_never_creates_preservable_contract(tmp_path, total, paths):
    gate = ledger(tmp_path, payload(total))
    result, issue = validate(gate, f"125 / {total} * 100", 125 / total * 100, paths)
    assert "derive_formula" not in issue
    assert "derive_operand_refs" not in issue
    assert not directive_for_issue(issue).preserve
    assert not CorrectionContract.from_validation(result).required_figures


@pytest.mark.parametrize("missing", [None, A, B, TOTAL])
def test_sum_requires_each_financial_operand(tmp_path, missing):
    gate = ledger(tmp_path, payload())
    paths = [path for path in [A, B, TOTAL] if path != missing]
    result, issue = validate(gate, "(125 + 250) / 10000 * 100", 3.75, paths, entity="Report")
    assert directive_for_issue(issue).preserve is (missing is None)
    if missing is None:
        assert issue["derive_operand_refs"] == ["call_ABC::" + path for path in paths]
        assert gate.revalidate(corrected(issue)).valid
    else:
        assert not CorrectionContract.from_validation(result).required_figures


@pytest.mark.parametrize("paths", [[A, TOTAL], [B, TOTAL], [A, B, TOTAL]])
def test_equal_valued_entities_cannot_substitute_or_hide_a_missing_leaf(tmp_path, paths):
    data = payload()
    data["data"]["rows"][1]["market_value"] = 125
    gate = ledger(tmp_path, data)
    formula = "(125 + 125) / 10000 * 100" if paths != [B, TOTAL] else "125 / 10000 * 100"
    _, issue = validate(gate, formula, 2.5 if paths != [B, TOTAL] else 1.25, paths)
    assert not directive_for_issue(issue).preserve


@pytest.mark.parametrize("formula", [
    "125 * (1 / 10000) * 100",
    "(125 / 10000) * 100",
    "125 / (10000 * 0.5) * 50",
    "125 / 5000 * 50",
])
def test_reciprocals_and_reassociation_do_not_hide_unreferenced_denominators(tmp_path, formula):
    gate = ledger(tmp_path, payload())
    _, issue = validate(gate, formula, 1.25, [A])
    assert not directive_for_issue(issue).preserve


@pytest.mark.parametrize("factor", ["0.25", "0.50", "100", "(1 - 0.25)"])
def test_mathematical_factors_do_not_require_financial_evidence(tmp_path, factor):
    gate = ledger(tmp_path, payload())
    value = 125 * eval(factor, {"__builtins__": {}}, {})
    _, issue = validate(gate, "125 * " + factor, value, [A])
    assert directive_for_issue(issue).preserve
    assert issue["derive_operand_refs"] == ["call_ABC::" + A]
    assert gate.revalidate(corrected(issue)).valid


def test_scalar_division_is_unsupported_for_repair_without_typed_operand_evidence(tmp_path):
    gate = ledger(tmp_path, payload())
    _, issue = validate(gate, "125 / 2", 62.5, [A])
    assert not directive_for_issue(issue).preserve
    # Preserve algebra behavior: this audit-specific boundary is not a new
    # scalar bound, nor a claim that a mathematical 2 is financial evidence.
    text = "ENTITY_A: 62.5.\n```figures\n62.5 | derived | 125 / 2 | call_ABC::" + A + "\n```"
    assert gate.revalidate(text).valid


def test_contract_requires_exact_expected_ref_set_after_provider_alias_normalization(tmp_path):
    gate = ledger(tmp_path, payload())
    result, issue = validate(gate, "125 / 10000 * 100", 1.25, [A, TOTAL])
    revised = corrected(issue)
    assert not CorrectionContract.from_validation(result).missing_figures(revised)
    extra = revised.replace("\n```", "; call_ABC::" + B + "\n```")
    assert CorrectionContract.from_validation(result).missing_figures(extra)


@pytest.mark.parametrize("total", [1, 100, 125])
def test_denominator_coverage_does_not_depend_on_magnitude_or_scalar_range(tmp_path, total):
    gate = ledger(tmp_path, payload(total))
    _, issue = validate(gate, f"125 / {total} * 100", 125 / total * 100, [A])
    assert not directive_for_issue(issue).preserve


def test_math_factor_is_not_evidence_merely_because_a_session_value_matches(tmp_path):
    gate = ledger(tmp_path, payload(total=2))
    _, issue = validate(gate, "125 * 2", 250, [A])
    assert directive_for_issue(issue).preserve
    assert issue["derive_operand_refs"] == ["call_ABC::" + A]
