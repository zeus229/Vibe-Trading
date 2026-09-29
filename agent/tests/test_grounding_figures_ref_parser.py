"""Regression coverage for figures refs containing provider call-id separators."""

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.figures import parse_figures_block


def test_figures_ref_keeps_pipe_inside_call_id() -> None:
    block = parse_figures_block(
        "```figures\n"
        "2.96% | observed | VaR 95% | call_alpha|fc_beta::data.tail_risk.var_95\n"
        "```"
    )

    assert block.malformed == ()
    assert len(block.declarations) == 1
    assert block.declarations[0].ref == "call_alpha|fc_beta::data.tail_risk.var_95"


@pytest.mark.parametrize("call_id", ["call_alpha", "call_alpha|fc_beta"])
@pytest.mark.parametrize("reference_style", ["field", "tool"])
def test_call_id_references_ground_observed_valuations(
    tmp_path: Path, call_id: str, reference_style: str
) -> None:
    """Both call and tool refs must ground currency-valued boundary fields."""
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Report the observed account values.")
    ledger.ingest_tool_result(
        tool_name="account_summary",
        arguments={},
        result=json.dumps(
            {
                "status": "ok",
                "data": {
                    "account": {
                        "value_start": 1250.75,
                        "value_end": 1400.25,
                        "currency": "USD",
                    }
                },
            }
        ),
        call_id=call_id,
        success=True,
    )
    ref_start = (
        f"{call_id}::data.account.value_start"
        if reference_style == "field"
        else "account_summary"
    )
    ref_end = (
        f"{call_id}::data.account.value_end"
        if reference_style == "field"
        else "account_summary"
    )
    result = ledger.validate_final_answer(
        "| Metric | Amount |\n"
        "|---|---:|\n"
        "| Opening value | 1250.75 USD |\n"
        "| Closing value | 1400.25 USD |\n\n"
        "```figures\n"
        f"1250.75 | observed | opening value | {ref_start}\n"
        f"1400.25 | observed | closing value | {ref_end}\n"
        "```"
    )

    assert result.valid is True, result.issues
    assert not any(issue.get("reason") == "not_in_referenced_call" for issue in result.issues)
