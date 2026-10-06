"""Regression coverage for the 2026-10-06 heterogeneous grounding campaign."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.figures import Figure
from src.agent.grounding.repair_contract import RepairAction, directive_for_issue

pytestmark = pytest.mark.unit


def _ingest(
    ledger: GroundingLedger,
    tool: str,
    payload: dict,
    call_id: str,
    *,
    arguments: dict | None = None,
) -> None:
    ledger.ingest_tool_result(
        tool_name=tool,
        arguments=arguments or {},
        result=json.dumps(payload),
        call_id=call_id,
        success=True,
    )


def test_unique_pre_pipe_call_id_prefix_resolves_to_full_provider_call_id(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="compare risk")
    full = "call_alpha|fc_beta"
    _ingest(
        ledger,
        "portfolio_risk",
        {"data": {"volatility": {"annualized_vol": 0.3581}}},
        full,
    )

    result = ledger.validate_final_answer(
        "Volatility is 35.81%.\n\n"
        "```figures\n"
        "35.81% | observed | annualized volatility | "
        "call_alpha::data.volatility.annualized_vol\n"
        "```"
    )

    assert result.valid is True, result.issues


def test_ambiguous_pre_pipe_call_id_prefix_remains_invalid(tmp_path: Path) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="compare risk")
    for call_id, value in (("call_alpha|fc_one", 0.3581), ("call_alpha|fc_two", 0.2308)):
        _ingest(
            ledger,
            "portfolio_risk",
            {"data": {"volatility": {"annualized_vol": value}}},
            call_id,
        )

    result = ledger.validate_final_answer(
        "Volatility is 35.81%.\n\n"
        "```figures\n"
        "35.81% | observed | annualized volatility | "
        "call_alpha::data.volatility.annualized_vol\n"
        "```"
    )

    assert result.valid is False
    assert any(issue.get("reason") == "unknown_call_id" for issue in result.issues)


def test_same_parent_value_twin_repairs_display_to_money_leaf(tmp_path: Path) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio value")
    call_id = "call_summary|fc_value"
    _ingest(
        ledger,
        "portfolio_summary",
        {
            "context": {
                "totals": {
                    "display": 222548671.772,
                    "usd": 0.0,
                    "cny": 0.0,
                    "native_by_currency": {"ARS": 222548671.772},
                }
            }
        },
        call_id,
    )
    figure = Figure(
        text="222.548.671,77",
        value=222548671.77,
        percent=False,
        start=0,
        end=14,
        line=0,
        shape="measured",
        digits="222548671.77",
        currency=True,
    )

    refs = ledger._same_parent_value_twin_candidates(
        "functions.portfolio_summary::context.totals.display",
        figure,
        None,
    )

    assert refs == [f"{call_id}::context.totals.native_by_currency.ARS"]


def test_same_parent_value_twin_does_not_borrow_wrong_currency_value(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="currency exposure")
    call_id = "call_summary|fc_currency"
    _ingest(
        ledger,
        "portfolio_summary",
        {
            "context": {
                "totals": {
                    "display": 57.87,
                    "native_by_currency": {
                        "ARS": 57.87,
                        "USD": 42.13,
                    },
                }
            }
        },
        call_id,
    )
    figure = Figure(
        text="42,13",
        value=42.13,
        percent=False,
        start=0,
        end=5,
        line=0,
        shape="measured",
        digits="42.13",
        currency=True,
    )

    assert ledger._same_parent_value_twin_candidates(
        "functions.portfolio_summary::context.totals.display",
        figure,
        None,
    ) == []


def test_total_row_label_is_not_promoted_to_pseudo_symbol(tmp_path: Path) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")
    calc_id = "call_calc|fc_sum"
    _ingest(
        ledger,
        "financial_rigor",
        {"status": "ok", "command": "calc", "expr": "0.2+0.13+0.06+0.059+0.0565", "result": 0.5055, "result_exact": "0.5055"},
        calc_id,
        arguments={"command": "calc", "expr": "0.2+0.13+0.06+0.059+0.0565"},
    )

    result = ledger.validate_final_answer(
        "| Ticker | Weight |\n"
        "|---|---:|\n"
        "| **Total conjunto** | **50.55%** |\n\n"
        "```figures\n"
        f"50.55% | observed | sum returned by calc | {calc_id}::result\n"
        "```"
    )

    assert result.valid is True, result.issues


def test_undeclared_deterministic_calc_gets_derive_repair_metadata(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="risk difference")
    _ingest(
        ledger,
        "portfolio_risk",
        {"data": {"volatility": {"annualized_vol": 0.3581475391315947}}},
        "call_actions|fc_risk",
    )
    _ingest(
        ledger,
        "portfolio_risk",
        {"data": {"volatility": {"annualized_vol": 0.2308300034551068}}},
        "call_cedears|fc_risk",
    )
    expr = "0.3581475391315947-0.2308300034551068"
    _ingest(
        ledger,
        "financial_rigor",
        {
            "status": "ok",
            "command": "calc",
            "expr": expr,
            "result": 0.1273175356764879,
            "result_exact": "0.1273175356764879",
        },
        "call_calc|fc_diff",
        arguments={"command": "calc", "expr": expr},
    )
    figure = Figure(
        text="12,73",
        value=12.73,
        percent=False,
        start=0,
        end=5,
        line=0,
        shape="measured",
        digits="12.73",
    )

    metadata = ledger._deterministic_derive_metadata(figure)
    issue = {
        "reason": "undeclared",
        "value": figure.text,
        "figure_value": figure.value,
        "figure_percent": figure.percent,
        "figure_currency": False,
        "figure_digits": figure.digits,
        **metadata,
    }
    directive = directive_for_issue(issue)

    assert metadata["derive_formula"] == f"({expr}) * 100"
    assert metadata["derive_operand_refs"] == [
        "call_actions|fc_risk::data.volatility.annualized_vol",
        "call_cedears|fc_risk::data.volatility.annualized_vol",
    ]
    assert directive.action is RepairAction.DERIVE
    assert directive.preserve is True
    assert directive.allowed_refs == tuple(metadata["derive_operand_refs"])


def test_calc_only_matching_under_broad_relative_tolerance_is_not_proven(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")
    for call_id, result in (("call_old|fc_calc", 0.50666507), ("call_new|fc_calc", 0.50550059)):
        _ingest(
            ledger,
            "financial_rigor",
            {"status": "ok", "command": "calc", "expr": "0.2+0.1+0.1+0.05+0.05", "result": result},
            call_id,
            arguments={"command": "calc", "expr": "0.2+0.1+0.1+0.05+0.05"},
        )
    figure = Figure(
        text="50,55%",
        value=50.55,
        percent=True,
        start=0,
        end=6,
        line=0,
        shape="measured",
        digits="50.55",
    )

    metadata = ledger._exact_repair_metadata(
        ["call_old|fc_calc::result", "call_new|fc_calc::result"],
        figure,
        None,
    )

    assert metadata["proven_ref_repair_candidates"] == ["call_new|fc_calc::result"]
    assert metadata["exact_ref_repair_candidate"] == "call_new|fc_calc::result"
