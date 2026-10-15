"""Synthetic regression cases for exact-ref claims near another entity."""

from __future__ import annotations

import json

import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit


PERFORMANCE_CALL = "call_perf"
POLICY_CALL = "call_policy"
MARKET_CALL = "call_market"


def _ledger(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio close report")
    payload = {
        "data": {
            "portfolio": {"return_pct": -2.97},
            "benchmarks": {
                "MERVAL_ARS": {"symbol": "MERVAL", "benchmark_return_pct": -7.92},
                "SPY_ARS": {"symbol": "SPY", "benchmark_return_pct": 3.72},
            },
            "positions": [
                {"symbol": "YPFD", "weight_pct": 20.61},
                {"symbol": "MERVAL", "return_pct": -7.92},
                {"symbol": "SPY", "return_pct": 3.72},
            ],
            "canonical_context": {
                "composition": {
                    "concentration": {"top5_pct": 52.47},
                    "by_instrument_type_pct": {"BONOS": 2.02, "FCI": 4.04},
                }
            },
        }
    }
    ledger.ingest_tool_result(
        tool_name="mcp_asistente_casa_consultar_performance_cartera_scope",
        arguments={}, result=json.dumps(payload), call_id=PERFORMANCE_CALL, success=True,
    )
    ledger.ingest_tool_result(
        tool_name="asistente_casa_investor_policy",
        arguments={},
        result=json.dumps({"policy": {"targets": [{"item_key": "Top 5", "max_pct": 55.0}]}}),
        call_id=POLICY_CALL, success=True,
    )
    ledger.ingest_tool_result(
        tool_name="mcp_asistente_casa_consultar_contexto_mercado_canonico",
        arguments={}, result=json.dumps({"data": {"items": {"vix": {"value": 17.56}}}}),
        call_id=MARKET_CALL, success=True,
    )
    ledger.ingest_tool_result(
        tool_name="get_market_data",
        arguments={"codes": ["SPY"]},
        result=json.dumps({"SPY": [{"trade_date": "2026-10-15", "close": 600.0}]}),
        call_id="call_spy_price", success=True,
    )
    return ledger


def _block(*rows):
    return "\n\n```figures\n" + "\n".join(rows) + "\n```"


def test_exact_refs_survive_unrelated_symbols_elsewhere_on_the_line(tmp_path):
    ledger = _ledger(tmp_path)
    prose = (
        "En los últimos treinta días, la cartera retrocede -2,97%, pero el "
        "Merval registra -7,92%. La referencia internacional en pesos avanza +3,72%.\n"
        "Concentración: YPFD es la principal posición; las cinco posiciones principales "
        "reúnen 52,47%, frente al máximo de política de 55,00%.\n"
        "La participación de bonos es 2,02% y la participación de fondos comunes "
        "de inversión es 4,04%.\n"
        "El VIX se ubicó en 17,56 puntos. SPY tuvo una rueda negativa."
    )
    result = ledger.validate_final_answer(
        prose
        + _block(
            f"-2.97% | observed | cartera 30d | {PERFORMANCE_CALL}::data.portfolio.return_pct",
            f"-7.92% | observed | Merval 30d | {PERFORMANCE_CALL}::data.benchmarks.MERVAL_ARS.benchmark_return_pct",
            f"3.72% | observed | SPY ARS 30d | {PERFORMANCE_CALL}::data.benchmarks.SPY_ARS.benchmark_return_pct",
            f"52.47% | observed | five largest aggregate | {PERFORMANCE_CALL}::data.canonical_context.composition.concentration.top5_pct",
            f"55.00% | observed | Top 5 policy maximum | {POLICY_CALL}::policy.targets[0].max_pct",
            f"2.02% | observed | Bonds share | {PERFORMANCE_CALL}::data.canonical_context.composition.by_instrument_type_pct.BONOS",
            f"4.04% | observed | FCI share | {PERFORMANCE_CALL}::data.canonical_context.composition.by_instrument_type_pct.FCI",
            f"17.56 | observed | VIX level | {MARKET_CALL}::data.items.vix.value",
        )
    )
    assert result.valid is True, [
        (issue.get("value"), issue.get("reason"), issue.get("symbol"))
        for issue in result.issues
    ]


@pytest.mark.parametrize(
    ("prose", "row", "expected_reason"),
    [
        (
            "Merval retrocedió -2,97%.",
            f"-2.97% | observed | cartera 30d | {PERFORMANCE_CALL}::data.portfolio.return_pct",
            "aggregate_ref_needs_unscoped_claim",
        ),
        (
            "YPFD representa 52,47%.",
            f"52.47% | observed | Top 5 aggregate | {PERFORMANCE_CALL}::data.canonical_context.composition.concentration.top5_pct",
            "aggregate_ref_needs_unscoped_claim",
        ),
        (
            "Merval subió +3,72%.",
            f"3.72% | observed | SPY ARS 30d | {PERFORMANCE_CALL}::data.benchmarks.SPY_ARS.benchmark_return_pct",
            "symbol_mismatch",
        ),
        (
            "SPY closed at 17.56 points.",
            f"17.56 | observed | VIX level | {MARKET_CALL}::data.items.vix.value",
            "not_in_referenced_call",
        ),
    ],
)
def test_explicit_wrong_entity_remains_rejected(tmp_path, prose, row, expected_reason):
    result = _ledger(tmp_path).validate_final_answer(prose + _block(row))
    assert result.valid is False
    assert expected_reason in {issue.get("reason") for issue in result.issues}
