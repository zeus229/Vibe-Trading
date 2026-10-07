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
    # A producer's candidate alone is not a preservation authorization.
    assert directive.preserve is False
    assert not directive.allowed_refs


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


@pytest.mark.parametrize("label", ["AAPL", "TSLA", "ZZZZ", "Acme Corp"])
def test_unknown_entity_like_row_label_still_blocks_cross_entity_borrow(
    tmp_path: Path, label: str
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="show holdings")
    _ingest(
        ledger,
        "portfolio_summary",
        {
            "positions": [
                {"symbol": "YPFD", "weight": 0.20},
            ]
        },
        "call_summary|fc_holdings",
    )

    result = ledger.validate_final_answer(
        "| Ticker | Weight |\n"
        "|---|---:|\n"
        f"| {label} | 20.00% |\n\n"
        "```figures\n"
        "20.00% | observed | weight | "
        "call_summary|fc_holdings::positions[0].weight\n"
        "```"
    )

    assert result.valid is False
    assert any(
        issue.get("reason") == "entity_ref_needs_scoped_claim"
        for issue in result.issues
    ), result.issues


def test_non_ticker_summary_row_may_stay_unscoped_for_unscoped_calc(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")
    calc_id = "call_calc|fc_sum"
    _ingest(
        ledger,
        "financial_rigor",
        {
            "status": "ok",
            "command": "calc",
            "expr": "0.2+0.13+0.06+0.059+0.0565",
            "result": 0.5055,
            "result_exact": "0.5055",
        },
        calc_id,
        arguments={"command": "calc", "expr": "0.2+0.13+0.06+0.059+0.0565"},
    )

    result = ledger.validate_final_answer(
        "| Ticker | Weight |\n"
        "|---|---:|\n"
        "| Total conjunto | 50.55% |\n\n"
        "```figures\n"
        f"50.55% | observed | sum returned by calc | {calc_id}::result\n"
        "```"
    )

    assert result.valid is True, result.issues


@pytest.mark.parametrize(
    "label",
    ["Total", "Subtotal", "Suma", "Total conjunto", "Suma de las cinco"],
)
def test_explicit_summary_labels_can_use_unscoped_calc_candidates(
    tmp_path: Path, label: str
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")
    calc_id = "call_calc|fc_summary"
    _ingest(
        ledger,
        "financial_rigor",
        {
            "status": "ok",
            "command": "calc",
            "expr": "0.2+0.13+0.06+0.059+0.0565",
            "result": 0.5055,
            "result_exact": "0.5055",
        },
        calc_id,
        arguments={"command": "calc", "expr": "0.2+0.13+0.06+0.059+0.0565"},
    )

    result = ledger.validate_final_answer(
        "| Ticker | Weight |\n"
        "|---|---:|\n"
        f"| {label} | 50.55% |\n\n"
        "```figures\n"
        "50.55% | observed | sum returned by calc | financial_rigor::result\n"
        "```"
    )

    assert result.valid is False
    assert len(result.issues) == 1
    issue = result.issues[0]
    assert issue.get("reason") == "field_ref_needs_call_id"
    assert issue.get("symbol") is None
    assert issue.get("exact_ref_repair_candidate") == f"{calc_id}::result"


def test_company_name_row_is_not_treated_as_summary_even_for_unscoped_calc(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")
    calc_id = "call_calc|fc_company"
    _ingest(
        ledger,
        "financial_rigor",
        {
            "status": "ok",
            "command": "calc",
            "expr": "0.2+0.13+0.06+0.059+0.0565",
            "result": 0.5055,
            "result_exact": "0.5055",
        },
        calc_id,
        arguments={"command": "calc", "expr": "0.2+0.13+0.06+0.059+0.0565"},
    )

    result = ledger.validate_final_answer(
        "| Ticker | Weight |\n"
        "|---|---:|\n"
        "| Acme Corp | 50.55% |\n\n"
        "```figures\n"
        f"50.55% | observed | aggregate calc | {calc_id}::result\n"
        "```"
    )

    assert result.valid is False
    assert any(
        issue.get("reason")
        in {
            "not_in_referenced_call",
            "entity_ref_needs_scoped_claim",
            "aggregate_ref_needs_unscoped_claim",
        }
        for issue in result.issues
    )



def test_calc_result_ref_cannot_replace_missing_derived_operand_refs(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")

    weights = [
        ("YPFD", 0.19968844),
        ("PAMP", 0.1311),
        ("GGAL", 0.0598),
        ("TGSU2", 0.0591),
        ("EWZ", 0.0564519),
    ]
    for index, (symbol, weight) in enumerate(weights):
        _ingest(
            ledger,
            "portfolio_summary",
            {"position": {"symbol": symbol, "weight": weight}},
            f"call_pos_{index}|fc_weight",
        )

    expr = "+".join(str(weight) for _, weight in weights)
    result_value = sum(weight for _, weight in weights) * 100.0
    _ingest(
        ledger,
        "financial_rigor",
        {
            "status": "ok",
            "command": "calc",
            "expr": expr,
            "result": result_value,
            "result_exact": str(result_value),
        },
        "call_calc|fc_top5",
        arguments={"command": "calc", "expr": expr},
    )

    answer = (
        f"Las cinco posiciones suman {result_value:.6f}%.\n\n"
        "```figures\n"
        f"{result_value:.6f}% | derived | "
        + " + ".join(str(weight) for _, weight in weights)
        + " | financial_rigor::result\n"
        "```"
    )

    validation = ledger.validate_final_answer(answer)
    assert validation.valid is False
    assert len(validation.issues) == 1
    issue = validation.issues[0]
    assert issue.get("reason") == "no_evidence"
    # The old test expected calc to invent all omitted refs. That violated
    # complete structural coverage; recovery must first obtain a declaration.
    assert not issue.get("derive_formula")
    assert not issue.get("derive_operand_refs")
    assert not directive_for_issue(issue).preserve


def test_multi_entity_derived_refs_ignore_incidental_ticker_on_same_line(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")

    positions = [
        {"symbol": "YPFD", "weight": 0.1997},
        {"symbol": "PAMP", "weight": 0.1311},
        {"symbol": "GGAL", "weight": 0.0598},
        {"symbol": "TGSU2", "weight": 0.0591},
        {"symbol": "EWZ", "weight": 0.0565},
        {"symbol": "JPM", "weight": 0.0296},
    ]
    call_id = "call_summary|fc_positions"
    _ingest(
        ledger,
        "portfolio_summary",
        {"positions": positions},
        call_id,
    )

    refs = ";".join(
        f"{call_id}::positions[{index}].weight"
        for index in range(5)
    )
    formula = "0.1997+0.1311+0.0598+0.0591+0.0565"
    total = 50.62

    answer = (
        f"**En conjunto suman {total:.2f}% de la cartera.** "
        "JPM ya figura en el snapshot actualizado, pero no integra las cinco "
        "posiciones principales.\n\n"
        "```figures\n"
        f"{total:.2f}% | derived | {formula} | {refs}\n"
        "```"
    )

    validation = ledger.validate_final_answer(answer)
    assert validation.valid is True, validation.issues


def test_same_entity_derived_refs_do_not_ignore_incidental_other_ticker(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="compare holdings")

    _ingest(
        ledger,
        "portfolio_summary",
        {
            "positions": [
                {"symbol": "YPFD", "weight": 0.20, "return_pct": 0.10},
                {"symbol": "JPM", "weight": 0.03, "return_pct": 0.02},
            ]
        },
        "call_summary|fc_same_entity",
    )

    answer = (
        "El agregado es 30.00%. JPM aparece también en el snapshot.\n\n"
        "```figures\n"
        "30.00% | derived | (0.20 + 0.10) * 100 | "
        "call_summary|fc_same_entity::positions[0].weight;"
        "call_summary|fc_same_entity::positions[0].return_pct\n"
        "```"
    )

    validation = ledger.validate_final_answer(answer)
    assert validation.valid is False
    assert any(
        issue.get("symbol") == "JPM"
        for issue in validation.issues
    ), validation.issues



def test_risk_normalized_weight_gets_canonical_entity_replacement(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")
    call_id = "call_summary|fc_weights"
    _ingest(
        ledger,
        "portfolio_summary",
        {
            "context": {
                "holdings_native": {
                    "ARS": [
                        {"symbol": "YPFD", "weight": 0.1997},
                        {"symbol": "PAMP", "weight": 0.1311},
                    ]
                },
                "risk_xray_args": {
                    "weights": {"YPFD": 0.2001, "PAMP": 0.1315}
                },
            }
        },
        call_id,
    )

    validation = ledger.validate_final_answer(
        "| Ticker | Peso |\n"
        "|---|---:|\n"
        "| YPFD | 20.01% |\n\n"
        "```figures\n"
        "20.01% | observed | portfolio weight | "
        "context.risk_xray_args.weights.YPFD\n"
        "```"
    )

    assert validation.valid is False
    issue = next(
        item for item in validation.issues
        if item.get("reason") == "entity_value_needs_canonical_replacement"
    )
    assert issue["replacement_text"] == "19.97%"
    assert issue["replacement_value"] == pytest.approx(19.97)
    assert issue["replacement_entity_symbol"] == "YPFD"
    assert issue["replacement_ref_candidate"] == (
        f"{call_id}::context.holdings_native.ARS[0].weight"
    )

    directive = directive_for_issue(issue)
    assert directive.action is RepairAction.REPLACE
    assert directive.preserve is True
    assert directive.replacement_value == pytest.approx(19.97)
    assert directive.exact_ref == issue["replacement_ref_candidate"]


def test_derived_summary_from_risk_weights_replaces_with_canonical_holdings_sum(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top five holdings")
    call_id = "call_summary|fc_aggregate_weights"
    holdings = [
        ("YPFD", 0.19968844),
        ("PAMP", 0.1310551),
        ("GGAL", 0.0598),
        ("TGSU2", 0.0591),
        ("EWZ", 0.05652953),
    ]
    risk_weights = {
        "YPFD": 0.2001,
        "PAMP": 0.1314,
        "GGAL": 0.0599,
        "TGSU2": 0.0592,
        "EWZ": 0.0567,
    }
    _ingest(
        ledger,
        "portfolio_summary",
        {
            "context": {
                "holdings_native": {
                    "ARS": [
                        {"symbol": symbol, "weight": weight}
                        for symbol, weight in holdings
                    ]
                },
                "risk_xray_args": {"weights": risk_weights},
            }
        },
        call_id,
    )

    risk_total = sum(risk_weights.values()) * 100
    refs = ";".join(
        f"context.risk_xray_args.weights.{symbol}"
        for symbol in risk_weights
    )
    formula = "(" + "+".join(str(value) for value in risk_weights.values()) + ")*100"

    validation = ledger.validate_final_answer(
        "| Ticker | Peso |\n"
        "|---|---:|\n"
        f"| **Total conjunto** | {risk_total:.2f}% |\n\n"
        "```figures\n"
        f"{risk_total:.2f}% | derived | {formula} | {refs}\n"
        "```"
    )

    assert validation.valid is False
    issue = next(
        item for item in validation.issues
        if item.get("reason") == "derived_summary_needs_canonical_replacement"
    )
    canonical_total = sum(weight for _, weight in holdings) * 100
    assert issue["replacement_value"] == pytest.approx(canonical_total)
    assert issue["replacement_text"] == f"{canonical_total:.2f}%"
    assert issue["replacement_role"] == "derived"
    assert len(issue["replacement_refs"]) == 5
    assert all("holdings_native" in ref for ref in issue["replacement_refs"])

    directive = directive_for_issue(issue)
    assert directive.action is RepairAction.REPLACE
    assert directive.preserve is True
    assert directive.replacement_value == pytest.approx(canonical_total)
    assert directive.replacement_formula == issue["replacement_formula"]
    assert set(directive.allowed_refs) == set(issue["replacement_refs"])


def test_deterministic_derive_requires_every_proven_operand_ref(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="risk difference")
    left_id = "call_left|fc_risk"
    right_id = "call_right|fc_risk"
    left = 0.3581
    right = 0.2308
    _ingest(
        ledger,
        "portfolio_risk",
        {"data": {"volatility": {"annualized_vol": left}}},
        left_id,
    )
    _ingest(
        ledger,
        "portfolio_risk",
        {"data": {"volatility": {"annualized_vol": right}}},
        right_id,
    )
    expr = f"{left}-{right}"
    _ingest(
        ledger,
        "financial_rigor",
        {
            "status": "ok",
            "command": "calc",
            "expr": expr,
            "result": left - right,
        },
        "call_calc|fc_diff",
        arguments={"command": "calc", "expr": expr},
    )

    validation = ledger.validate_final_answer(
        "La diferencia es 12.73%.\n\n"
        "```figures\n"
        "12.73% | derived | 0.3581-0.2308 | "
        f"{left_id}::data.volatility.annualized_vol\n"
        "```"
    )

    assert validation.valid is False
    # A complete calc cannot turn a partial declaration into a promise.
    assert all(not directive_for_issue(issue).preserve for issue in validation.issues)
    assert all(not issue.get("derive_operand_refs") for issue in validation.issues)


def test_bare_risk_weight_ref_must_match_claim_ticker_for_replacement(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top holdings")
    call_id = "call_summary|fc_mismatch"
    _ingest(
        ledger,
        "portfolio_summary",
        {
            "context": {
                "holdings_native": {
                    "ARS": [
                        {"symbol": "YPFD", "weight": 0.1997},
                        {"symbol": "PAMP", "weight": 0.1311},
                    ]
                },
                "risk_xray_args": {
                    "weights": {"YPFD": 0.2001, "PAMP": 0.1314}
                },
            }
        },
        call_id,
    )

    result = ledger.validate_final_answer(
        "| Ticker | Peso |\n"
        "|---|---:|\n"
        "| PAMP | 20.01% |\n\n"
        "```figures\n"
        "20.01% | observed | weight | context.risk_xray_args.weights.YPFD\n"
        "```"
    )

    assert result.valid is False
    assert not any(
        issue.get("reason") == "entity_value_needs_canonical_replacement"
        for issue in result.issues
    )


def test_bare_risk_weight_ref_requires_unique_source_call(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top holdings")
    for suffix, holding in (("one", 0.1997), ("two", 0.1988)):
        _ingest(
            ledger,
            "portfolio_summary",
            {
                "context": {
                    "holdings_native": {
                        "ARS": [
                            {"symbol": "YPFD", "weight": holding},
                            {"symbol": "PAMP", "weight": 0.13},
                        ]
                    },
                    "risk_xray_args": {
                        "weights": {"YPFD": 0.2001, "PAMP": 0.131}
                    },
                }
            },
            f"call_summary|fc_{suffix}",
        )

    result = ledger.validate_final_answer(
        "| Ticker | Peso |\n"
        "|---|---:|\n"
        "| YPFD | 20.01% |\n\n"
        "```figures\n"
        "20.01% | observed | weight | context.risk_xray_args.weights.YPFD\n"
        "```"
    )

    assert result.valid is False
    assert not any(
        issue.get("reason") == "entity_value_needs_canonical_replacement"
        for issue in result.issues
    )



def test_bare_display_ref_repairs_to_same_call_money_sibling(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio cuts")
    call_id = "call_summary|fc_snapshot"
    _ingest(
        ledger,
        "portfolio_summary",
        {
            "context": {
                "totals": {
                    "display": 222020616.326,
                    "native_by_currency": {"ARS": 222020616.326},
                }
            }
        },
        call_id,
    )

    result = ledger.validate_final_answer(
        "Snapshot actual: ARS 222.020.616,326.\n\n"
        "```figures\n"
        "222.020.616,326 | observed | snapshot value | context.totals.display\n"
        "```"
    )

    assert result.valid is False
    issue = next(
        item for item in result.issues
        if item.get("reason") == "field_ref_needs_numeric_sibling"
    )
    expected = f"{call_id}::context.totals.native_by_currency.ARS"
    assert issue["field_ref_candidates"] == [expected]
    directive = directive_for_issue(issue)
    assert directive.action is RepairAction.PRESERVE_REWRITE
    assert directive.preserve is True
    assert directive.exact_ref == expected
    assert directive.allowed_refs == (expected,)
    assert directive.target_scope == "aggregate"


def test_bare_display_ref_with_two_matching_source_calls_stays_fail_closed(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio cuts")
    for suffix in ("one", "two"):
        _ingest(
            ledger,
            "portfolio_summary",
            {
                "context": {
                    "totals": {
                        "display": 222020616.326,
                        "native_by_currency": {"ARS": 222020616.326},
                    }
                }
            },
            f"call_summary|fc_{suffix}",
        )

    result = ledger.validate_final_answer(
        "Snapshot actual: ARS 222.020.616,326.\n\n"
        "```figures\n"
        "222.020.616,326 | observed | snapshot value | context.totals.display\n"
        "```"
    )

    assert result.valid is False
    assert not any(
        item.get("reason") == "field_ref_needs_numeric_sibling"
        for item in result.issues
    )


def test_bare_scalar_metric_ref_gets_exact_call_candidate_without_reformatting(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="compare risk")
    call_id = "call_risk|fc_acciones"
    _ingest(
        ledger,
        "portfolio_risk",
        {
            "symbol": "ACCIONES",
            "data": {"volatility": {"annualized_vol": 0.35463698891476825}},
        },
        call_id,
    )
    _ingest(
        ledger,
        "portfolio_risk",
        {
            "symbol": "ACCIONES",
            "data": {"volatility": {"annualized_vol": 0.2272}},
        },
        "call_risk|fc_other",
    )

    result = ledger.validate_final_answer(
        "ACCIONES: 35.46%.\n\n"
        "```figures\n"
        "35.46% | observed | annualized volatility | data.volatility.annualized_vol\n"
        "```"
    )

    assert result.valid is False
    issue = next(
        item for item in result.issues
        if item.get("reason") == "field_ref_needs_call_id"
    )
    expected = f"{call_id}::data.volatility.annualized_vol"
    assert issue["field_ref_candidates"] == [expected]
    directive = directive_for_issue(issue)
    assert directive.action is RepairAction.AUTO_REPAIR
    assert directive.preserve is True
    assert directive.exact_ref == expected


def test_bare_scalar_metric_ref_with_multiple_matching_calls_preserves_options(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="compare risk")
    for suffix in ("one", "two"):
        _ingest(
            ledger,
            "portfolio_risk",
            {
                "symbol": "ACCIONES",
                "data": {"volatility": {"annualized_vol": 0.35463698891476825}},
            },
            f"call_risk|fc_{suffix}",
        )
    _ingest(
        ledger,
        "portfolio_risk",
        {
            "symbol": "ACCIONES",
            "data": {"volatility": {"annualized_vol": 0.2272}},
        },
        "call_risk|fc_other",
    )

    result = ledger.validate_final_answer(
        "ACCIONES: 35.46%.\n\n"
        "```figures\n"
        "35.46% | observed | annualized volatility | data.volatility.annualized_vol\n"
        "```"
    )

    assert result.valid is False
    issue = next(
        item for item in result.issues
        if item.get("reason") == "field_ref_needs_call_id"
    )
    assert len(issue.get("proven_ref_repair_candidates") or []) == 2
    directive = directive_for_issue(issue)
    assert directive.action is RepairAction.PRESERVE_OPTIONS
    assert directive.preserve is True


def test_bare_risk_weight_still_prefers_canonical_replace_over_exact_call_repair(
    tmp_path: Path,
) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="top holdings")
    call_id = "call_summary|fc_weights"
    _ingest(
        ledger,
        "portfolio_summary",
        {
            "context": {
                "holdings_native": {
                    "ARS": [{"symbol": "YPFD", "weight": 0.1997}]
                },
                "risk_xray_args": {"weights": {"YPFD": 0.2001}},
            }
        },
        call_id,
    )

    result = ledger.validate_final_answer(
        "| Ticker | Peso |\n"
        "|---|---:|\n"
        "| YPFD | 20.01% |\n\n"
        "```figures\n"
        "20.01% | observed | weight | context.risk_xray_args.weights.YPFD\n"
        "```"
    )

    assert result.valid is False
    issue = next(
        item for item in result.issues
        if item.get("reason") == "entity_value_needs_canonical_replacement"
    )
    directive = directive_for_issue(issue)
    assert directive.action is RepairAction.REPLACE
    assert directive.replacement_value == pytest.approx(19.97)
