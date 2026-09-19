"""Locale-aware numeric claims in the grounding gate (native BYMA E2E, 2026-09-19).

Three independent failures blocked a valid Spanish answer for a correct Risk X-Ray:

A. ``_numbers`` split a decimal comma with a long fraction ("2,639499655314571") in two.
B. A confidence level ("95%") had no legitimate way to be stated: it is a label of the
   ``var_95`` / ``expected_shortfall_95`` leaves, not a portfolio measurement.
C. ``effective_n`` declared ``derived`` with a symbolic note was rejected although the tool
   returned that exact value.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.figures import _numbers

pytestmark = pytest.mark.unit

RISK_XRAY = json.loads(
    r"""
{
    "status": "ok",
    "data": {
        "inputs": {
            "symbols": [
                "PAMP.BA",
                "GGAL.BA",
                "TGSU2.BA"
            ],
            "weights": {
                "PAMP.BA": 0.50742427,
                "GGAL.BA": 0.25198292,
                "TGSU2.BA": 0.24059281
            },
            "aligned_days": 250,
            "return_observations": 249
        },
        "concentration": {
            "hhi": 0.3788596819804554,
            "effective_n": 2.639499655314571,
            "top1_weight": 0.50742427,
            "top3_weight": 1.0
        },
        "volatility": {
            "daily_vol": 0.024279878009678714,
            "annualized_vol": 0.3854311144595747,
            "downside_deviation_annualized": 0.179318145214327
        },
        "drawdown": {
            "max_drawdown": -0.18406040871406562,
            "max_drawdown_start": "2025-12-03T00:00:00",
            "max_drawdown_trough": "2026-03-04T00:00:00"
        },
        "tail_risk": {
            "var_95": 0.03173415038642362,
            "expected_shortfall_95": 0.03736523857443003,
            "var_99": 0.03826411411284097,
            "expected_shortfall_99": 0.04579115414979293,
            "method": "historical simulation (non-parametric)"
        },
        "diversification": {
            "diversification_ratio": 1.1049744716901015
        },
        "correlation": {
            "avg_pairwise_abs": 0.7239869068509964,
            "max_pair": {
                "symbols": [
                    "PAMP.BA",
                    "TGSU2.BA"
                ],
                "corr": 0.8178292900359012
            },
            "beta_to_equal_weight": 0.9430231192601753
        },
        "skipped": [],
        "warnings": []
    }
}
"""
)
REF = "portfolio_risk_xray"
TAIL = RISK_XRAY["data"]["tail_risk"]


def _digits(text: str) -> list[str]:
    return [token.sign + token.digits for token in _numbers(text)]


def _es(value: float, places: int) -> str:
    """A fraction rendered as a Spanish percentage ("3,17%")."""
    return f"{value * 100:.{places}f}%".replace(".", ",")


def _ledger(tmp_path: Path) -> GroundingLedger:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analizá el riesgo de la cartera")
    ledger.ingest_tool_result(
        tool_name=REF,
        arguments={"symbols": ["PAMP.BA", "GGAL.BA", "TGSU2.BA"]},
        result=json.dumps(RISK_XRAY),
        call_id=REF,
        success=True,
    )
    return ledger


def _figures(*rows: str) -> str:
    return "\n\n```figures\n" + "\n".join(rows) + "\n```"


# --- A. decimal comma -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("3,17%", ["3.17"]),
        ("17,9318145214327%", ["17.9318145214327"]),
        ("2,639499655314571", ["2.639499655314571"]),
        ("0,7239869355", ["0.7239869355"]),
        ("-18,41%", ["-18.41"]),
        ("3.17%", ["3.17"]),
        ("17.9318145214327%", ["17.9318145214327"]),
        ("-18.41%", ["-18.41"]),
        ("1,234", ["1234"]),
        ("1,234.56", ["1234.56"]),
        ("1.234,56", ["1234.56"]),
        ("1,234,567", ["1234567"]),
        ("1.234.567,89", ["1234567.89"]),
        ("VaR 95%: 3,17%; ES 99%: 4,58%", ["95", "3.17", "99", "4.58"]),
        # Not decimals: a year list and a dotted list keep their separate readings.
        ("2023,2024", ["2023", "2024"]),
        ("0.500,0.600", ["0.500", "0.600"]),
    ],
)
def test_numbers_reads_each_localized_number_as_one_figure(text: str, expected: list[str]) -> None:
    assert _digits(text) == expected


def test_bare_decimal_comma_declaration_cell_is_one_figure(tmp_path: Path) -> None:
    result = _ledger(tmp_path).validate_final_answer(
        "Effective N: 2,64. Volatilidad: 38,54%."
        + _figures(
            f"38,54% | observed | annualized_vol | {REF}",
            f"2,64 | observed | effective_n | {REF}",
        )
    )
    assert result.valid is True, result.issues


def test_long_precision_spanish_percentage_is_verified_not_fragmented(tmp_path: Path) -> None:
    vol = RISK_XRAY["data"]["volatility"]["annualized_vol"]
    result = _ledger(tmp_path).validate_final_answer(f"Volatilidad anualizada: {_es(vol, 12)}.")
    assert result.valid is True, result.issues


def test_a_wrong_long_precision_spanish_percentage_is_still_rejected(tmp_path: Path) -> None:
    result = _ledger(tmp_path).validate_final_answer("Volatilidad anualizada: 41,123456789012%.")
    assert result.valid is False


# --- B. confidence levels ----------------------------------------------------------------


def test_confidence_levels_are_labels_not_portfolio_claims(tmp_path: Path) -> None:
    values = {
        "var_95": TAIL["var_95"],
        "var_99": TAIL["var_99"],
        "expected_shortfall_95": TAIL["expected_shortfall_95"],
        "expected_shortfall_99": TAIL["expected_shortfall_99"],
    }
    rows = [f"{_es(value, 2)} | observed | {name} | {REF}" for name, value in values.items()]
    rows += [f"95% | observed | confidence level | {REF}", f"99% | observed | confidence level | {REF}"]
    prose = (
        f"VaR 95%: {_es(TAIL['var_95'], 2)}. VaR 99%: {_es(TAIL['var_99'], 2)}. "
        f"ES 95%: {_es(TAIL['expected_shortfall_95'], 2)}. ES 99%: {_es(TAIL['expected_shortfall_99'], 2)}."
    )
    result = _ledger(tmp_path).validate_final_answer(prose + _figures(*rows))
    assert result.valid is True, result.issues


def test_undeclared_confidence_levels_pass_when_the_metric_values_are_declared(tmp_path: Path) -> None:
    var95, var99 = _es(TAIL["var_95"], 2), _es(TAIL["var_99"], 2)
    result = _ledger(tmp_path).validate_final_answer(
        f"VaR 95%: {var95}. VaR 99%: {var99}."
        + _figures(f"{var95} | observed | var_95 | {REF}", f"{var99} | observed | var_99 | {REF}")
    )
    assert result.valid is True, result.issues


def test_confidence_level_from_a_metadata_leaf_is_also_a_label(tmp_path: Path) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="riesgo")
    ledger.ingest_tool_result(
        tool_name="risk_tool",
        arguments={},
        result=json.dumps({"tail_risk": {"confidence_level": 0.95, "var": 0.0317341504}}),
        call_id="risk_tool",
        success=True,
    )
    result = ledger.validate_final_answer(
        "VaR 95%: 3.17%."
        + _figures("3.17% | observed | var | risk_tool", "95% | observed | confidence level | risk_tool")
    )
    assert result.valid is True, result.issues


def test_a_fabricated_confidence_level_is_still_rejected(tmp_path: Path) -> None:
    result = _ledger(tmp_path).validate_final_answer(
        "VaR 97%: 3.17%."
        + _figures(f"3.17% | observed | var_95 | {REF}", f"97% | observed | confidence level | {REF}")
    )
    assert result.valid is False
    assert any(issue.get("value") == "97%" for issue in result.issues)


def test_a_wrong_var_value_is_still_rejected_next_to_a_valid_confidence_level(tmp_path: Path) -> None:
    result = _ledger(tmp_path).validate_final_answer(
        "VaR 95%: 9.99%."
        + _figures(f"9.99% | observed | var_95 | {REF}", f"95% | observed | confidence level | {REF}")
    )
    assert result.valid is False
    assert any(issue.get("value") == "9.99%" for issue in result.issues)


def test_a_qualifier_does_not_ground_a_decimal_measurement(tmp_path: Path) -> None:
    result = _ledger(tmp_path).validate_final_answer(
        "El retorno fue 95.5%." + _figures(f"95.5% | observed | return | {REF}")
    )
    assert result.valid is False


# --- C. effective N -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        f"2.64 | derived | 1/HHI | {REF}",
        f"2.64 | derived | | {REF}",
        f"2,64 | derived | 1/HHI | {REF}",
    ],
)
def test_effective_n_returned_by_the_tool_needs_no_external_formula(tmp_path: Path, row: str) -> None:
    result = _ledger(tmp_path).validate_final_answer("Effective N: 2.64." + _figures(row))
    assert result.valid is True, result.issues


def test_derived_value_not_returned_by_any_tool_is_still_rejected(tmp_path: Path) -> None:
    result = _ledger(tmp_path).validate_final_answer(
        "Effective N: 3.50." + _figures(f"3.50 | derived | 1/HHI | {REF}")
    )
    assert result.valid is False
    assert any(issue.get("reason") == "formula_not_evaluable" for issue in result.issues)


def test_derived_with_an_unobserved_additive_operand_is_still_rejected(tmp_path: Path) -> None:
    result = _ledger(tmp_path).validate_final_answer(
        "Effective N: 2.64." + _figures(f"2.64 | derived | 0.379 + 7.77 - 5.5 | {REF}")
    )
    assert result.valid is False


# --- the reconstructed real answer ---------------------------------------------------------


def test_full_spanish_risk_xray_answer_with_full_precision_is_valid(tmp_path: Path) -> None:
    data = RISK_XRAY["data"]
    vol, tail, conc = data["volatility"], data["tail_risk"], data["concentration"]
    rows = [
        (_es(vol["annualized_vol"], 12), "annualized_vol"),
        (_es(vol["downside_deviation_annualized"], 12), "downside_deviation_annualized"),
        (_es(data["drawdown"]["max_drawdown"], 12), "max_drawdown"),
        (_es(tail["var_95"], 12), "var_95"),
        (_es(tail["var_99"], 12), "var_99"),
        (_es(tail["expected_shortfall_95"], 12), "expected_shortfall_95"),
        (_es(tail["expected_shortfall_99"], 12), "expected_shortfall_99"),
        (_es(conc["top1_weight"], 6), "top1_weight"),
        (str(conc["effective_n"]).replace(".", ","), "effective_n"),
        (str(data["diversification"]["diversification_ratio"]).replace(".", ","), "diversification_ratio"),
        (_es(data["correlation"]["avg_pairwise_abs"], 12), "avg_pairwise_abs"),
    ]
    lines = [f"- {name}: {value}" for value, name in rows]
    lines += ["- VaR 95% y VaR 99% son niveles de confianza; ES 95% y ES 99% también."]
    declared = [f"{value} | observed | {name} | {REF}" for value, name in rows]
    declared += [f"95% | observed | confidence level | {REF}", f"99% | observed | confidence level | {REF}"]
    result = _ledger(tmp_path).validate_final_answer("\n".join(lines) + _figures(*declared))
    assert result.valid is True, result.issues
