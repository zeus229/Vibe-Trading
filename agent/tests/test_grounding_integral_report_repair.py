"""Synthetic incident regressions for portfolio report grounding."""

from __future__ import annotations

import json

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.repair_contract import CorrectionContract
from tests.test_grounding_partial_contract_retry import _Grounding, _run
from src.agent.grounding.policies import ValidationResult

pytestmark = pytest.mark.unit


def _ledger(tmp_path, *, include_composition=False):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio close report")
    if include_composition:
        ledger.ingest_tool_result(
            tool_name="mcp_asistente_casa_consultar_performance_cartera_scope",
            arguments={"asset_type": "ALL", "period": "1r"},
            result=json.dumps({"data": {"canonical_context": {"composition": {
                "by_instrument_type_pct": {"EFECTIVO": 4.04, "ACCIONES": 95.96}
            }}}}),
            call_id="call_composition", success=True,
        )
    return ledger


def _block(*rows):
    return "\n\n```figures\n" + "\n".join(rows) + "\n```"


def test_all_scope_performance_stays_aggregate_after_composition(tmp_path):
    ledger = _ledger(tmp_path, include_composition=True)
    for period, value in (("mtd", 1.25), ("30d", -2.97), ("ytd", 8.4)):
        call = f"call_{period}"
        ledger.ingest_tool_result(
            tool_name="mcp_asistente_casa_consultar_performance_cartera_scope",
            arguments={"asset_type": "ALL", "period": period},
            result=json.dumps({"data": {"portfolio": {"return_pct": value}}}),
            call_id=call, success=True,
        )
        record = next(item for item in ledger._evidence if item.call_id == call)
        assert record.identity_scope == "aggregate"
        assert record.symbol is None

    report = (
        "TWR cartera MTD considerando efectivo: +1,25%. "
        "TWR cartera 30d: -2,97%. TWR cartera YTD: +8,40%."
        + _block(
            "1.25% | observed | TWR cartera MTD efectivo | call_mtd::data.portfolio.return_pct",
            "-2.97% | observed | TWR cartera 30d | call_30d::data.portfolio.return_pct",
            "8.4% | observed | TWR cartera YTD | call_ytd::data.portfolio.return_pct",
        )
    )
    result = ledger.validate_final_answer(report)
    assert result.valid, [(i.get("reason"), i.get("symbol"), i.get("value")) for i in result.issues]


def _contribution_ledger(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.ingest_tool_result(
        tool_name="mcp_asistente_casa_consultar_performance_cartera_scope",
        arguments={"asset_type": "ALL", "period": "1r"},
        result=json.dumps({"data": {"positions": [
            {"symbol": "GGAL", "contribution_pct": -0.092},
            {"symbol": "GPRK", "contribution_pct": -0.092},
        ]}}),
        call_id="call_attribution", success=True,
    )
    return ledger


def test_equal_contributions_keep_exact_entity_refs_across_summary_and_table(tmp_path):
    ref_a = "call_attribution::data.positions[0].contribution_pct"
    ref_b = "call_attribution::data.positions[1].contribution_pct"
    report = (
        "GGAL restó -0,092 pp; GPRK restó -0,092 pp.\n\n"
        "| Ticker | Contribution (pp) |\n| --- | ---: |\n"
        "| GGAL | -0,092 |\n| GPRK | -0,092 |"
        + _block(
            f"-0.092 | observed | GGAL contribution | {ref_a}",
            f"-0.092 | observed | GPRK contribution | {ref_b}",
        )
    )
    result = _contribution_ledger(tmp_path).validate_final_answer(report)
    assert result.valid, [(i.get("reason"), i.get("symbol"), i.get("value")) for i in result.issues]


def test_equal_contributions_reject_swapped_refs_and_invented_value(tmp_path):
    ref_a = "call_attribution::data.positions[0].contribution_pct"
    ref_b = "call_attribution::data.positions[1].contribution_pct"
    ledger = _contribution_ledger(tmp_path)
    swapped = (
        "| Ticker | Contribution (pp) |\n| --- | ---: |\n| GGAL | -0,092 |\n| GPRK | -0,092 |"
        + _block(
            f"-0.092 | observed | GGAL contribution | {ref_b}",
            f"-0.092 | observed | GPRK contribution | {ref_a}",
        )
    )
    wrong = (
        "GGAL contribuyó -0,093 pp."
        + _block(f"-0.093 | observed | GGAL contribution | {ref_a}")
    )
    assert not ledger.validate_final_answer(swapped).valid
    assert not ledger.validate_final_answer(wrong).valid


def test_correction_contract_preserves_clean_exact_ref_occurrences(tmp_path):
    ledger = _ledger(tmp_path)
    call = "call_30d"
    ledger.ingest_tool_result(
        tool_name="mcp_asistente_casa_consultar_performance_cartera_scope",
        arguments={"asset_type": "ALL", "period": "30d"},
        result=json.dumps({"data": {"portfolio": {"return_pct": -2.97}}}),
        call_id=call, success=True,
    )
    valid_claim = (
        "Cartera 30d: -2,97%.\n"
        + _block("-2.97% | observed | cartera 30d | call_30d::data.portfolio.return_pct")
    )
    # Add a separate bad claim so the agent enters the text-only correction path.
    rejected = valid_claim.replace("```figures", "El VIX fue 99,99.\n\n```figures") + "\n99.99 | observed | VIX | missing_call::data.vix.value"
    validation = ledger.validate_final_answer(rejected)
    assert not validation.valid
    assert any(claim["text"] == "-2,97%" for claim in validation.passed_claims)
    contract = CorrectionContract.from_validation(validation)

    omitted = "Informe corregido sin TWR.\n\n```figures\n```"
    assert "-2,97%" in contract.missing_figures(omitted)
    wrong_ref = (
        "Cartera 30d: -2,97%.\n"
        + _block("-2.97% | observed | cartera 30d | other_call::data.portfolio.return_pct")
    )
    assert "-2,97%" in contract.missing_figures(wrong_ref)
    preserved = (
        "Cartera 30d: -2,97%.\n"
        + _block("-2.97% | observed | cartera 30d | call_30d::data.portfolio.return_pct")
    )
    assert "-2,97%" not in contract.missing_figures(preserved)


def test_contract_does_not_exceed_max_iteration_budget(tmp_path):
    issue = {
        "code": "figure_undeclared", "reason": "figure_undeclared", "value": "12.5",
        "figure_value": 12.5, "figure_percent": False, "figure_digits": "12.5",
        "exact_ref_repair_candidate": "call_synthetic::data.metric",
    }
    contract = CorrectionContract.from_validation(ValidationResult(False, [issue]))
    grounding = _Grounding(issue, "call_synthetic::data.metric", final_valid=False)
    result = _run(
        grounding, "corrected but still invalid", revisions=0,
        contract=contract, contract_retries=0, iteration=8, max_revisions=2,
    )
    assert not result.retry
    assert result.contract_retries == 0
    assert grounding.redacted


def test_synthetic_daily_close_report_all_periods_benchmarks_and_risk_rows(tmp_path):
    ledger = _ledger(tmp_path)
    period_values = {"1r": 0.18, "mtd": 0.40, "30d": -2.97, "ytd": 4.20}
    refs = {}
    for period, value in period_values.items():
        call = f"call_perf_{period}"
        payload = {"data": {"portfolio": {"return_pct": value}}}
        if period == "30d":
            payload["data"].update({
                "benchmarks": {
                    "MERVAL_ARS": {"symbol": "MERVAL", "benchmark_return_pct": -7.92},
                    "SPY_ARS": {"symbol": "SPY", "benchmark_return_pct": 3.72},
                },
                "canonical_context": {"composition": {
                    "concentration": {"top5_pct": 52.47},
                    "sector_exposure_pct": {"ENERGIA": 54.23},
                    "by_instrument_type_pct": {"EFECTIVO": 2.0, "FCI": 4.04, "ACCIONES": 93.96},
                }},
            })
        ledger.ingest_tool_result(
            tool_name="mcp_asistente_casa_consultar_performance_cartera_scope",
            arguments={"asset_type": "ALL", "period": period}, result=json.dumps(payload),
            call_id=call, success=True,
        )
        refs[period] = f"{call}::data.portfolio.return_pct"
    ledger.ingest_tool_result(
        tool_name="asistente_casa_investor_policy", arguments={},
        result=json.dumps({"policy": {"targets": [{"item_key": "Top 5", "max_pct": 55.0}]}}),
        call_id="call_policy", success=True,
    )
    ledger.ingest_tool_result(
        tool_name="mcp_asistente_casa_consultar_performance_cartera_scope",
        arguments={"asset_type": "ALL", "period": "1r"},
        result=json.dumps({"data": {"positions": [
            {"symbol": "GGAL", "contribution_pct": -0.092},
            {"symbol": "GPRK", "contribution_pct": -0.092},
            {"symbol": "YPFD", "contribution_pct": 0.31},
        ]}}),
        call_id="call_attribution", success=True,
    )
    answer = (
        "Cartera: 1r +0,18%; MTD +0,40%; 30d -2,97%; YTD +4,20%. "
        "En 30d MERVAL ARS -7,92% y SPY ARS +3,72%.\n"
        "GGAL restó -0,092 pp; GPRK restó -0,092 pp; YPFD aportó +0,31 pp.\n"
        "Top 5 52,47% frente al límite 55,00%; Energía 54,23%; FCI 4,04%.\n"
        "| Ticker | Contribución (pp) |\n| --- | ---: |\n"
        "| GGAL | -0,092 |\n| GPRK | -0,092 |\n| YPFD | 0,31 |"
        + _block(
            f"0.18% | observed | TWR cartera 1r | {refs['1r']}",
            f"0.40% | observed | TWR cartera MTD | {refs['mtd']}",
            f"-2.97% | observed | TWR cartera 30d | {refs['30d']}",
            f"4.20% | observed | TWR cartera YTD | {refs['ytd']}",
            f"-7.92% | observed | MERVAL ARS 30d | call_perf_30d::data.benchmarks.MERVAL_ARS.benchmark_return_pct",
            f"3.72% | observed | SPY ARS 30d | call_perf_30d::data.benchmarks.SPY_ARS.benchmark_return_pct",
            "52.47% | observed | Top 5 concentration | call_perf_30d::data.canonical_context.composition.concentration.top5_pct",
            "55.00% | observed | Top 5 limit | call_policy::policy.targets[0].max_pct",
            "54.23% | observed | energy sector exposure | call_perf_30d::data.canonical_context.composition.sector_exposure_pct.ENERGIA",
            "4.04% | observed | FCI share | call_perf_30d::data.canonical_context.composition.by_instrument_type_pct.FCI",
            "-0.092 | observed | GGAL contribution | call_attribution::data.positions[0].contribution_pct",
            "-0.092 | observed | GPRK contribution | call_attribution::data.positions[1].contribution_pct",
            "0.31 | observed | YPFD contribution | call_attribution::data.positions[2].contribution_pct",
        )
    )
    result = ledger.validate_final_answer(answer)
    assert result.valid, [(i.get("reason"), i.get("symbol"), i.get("value")) for i in result.issues]



@pytest.mark.parametrize("unit", ["pp", "bp", "bps"])
@pytest.mark.parametrize("gap", ["", " ", "\t"])
def test_point_unit_marker_spacing_recognized(unit, gap):
    from src.agent.grounding.figures import _percent_mark

    token = f"-0.092{gap}{unit}"
    scale, end = _percent_mark(token, len("-0.092"))
    assert scale == (0.01 if unit in ("bp", "bps") else 1.0)
    assert end == len(token)


@pytest.mark.parametrize("suffix", ["ppm", "bpp", "bpsx", "pptrading"])
def test_point_mark_must_end_at_word_boundary(suffix):
    from src.agent.grounding.figures import _percent_mark

    scale, end = _percent_mark(f"-0.092 {suffix}", len("-0.092"))
    assert scale == 0.0
    assert end == len("-0.092")


def test_spaced_pp_equal_contributions_publish_with_exact_refs(tmp_path):
    ref_a = "call_attribution::data.positions[0].contribution_pct"
    ref_b = "call_attribution::data.positions[1].contribution_pct"
    text = (
        "GGAL restó -0,092 pp; GPRK restó -0,092 pp.\n\n"
        "| Instrumento | Contribución |\n| --- | ---: |\n"
        "| GGAL | -0,092 pp |\n| GPRK | -0,092 pp |"
        + _block(
            f"-0.092 pp | observed | GGAL contribución | {ref_a}",
            f"-0.092 pp | observed | GPRK contribución | {ref_b}",
        )
    )
    ledger = _contribution_ledger(tmp_path)
    assert ledger.validate_final_answer(text).valid
    swapped = text.replace(
        f"GGAL contribución | {ref_a}", f"GGAL contribución | {ref_b}"
    ).replace(
        f"GPRK contribución | {ref_b}", f"GPRK contribución | {ref_a}"
    )
    assert not ledger.validate_final_answer(swapped).valid
    fabricated = text.replace("-0,092 pp", "-0,093 pp", 1)
    assert not ledger.validate_final_answer(fabricated).valid
