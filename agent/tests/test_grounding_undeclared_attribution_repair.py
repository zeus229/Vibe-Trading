from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.figures import parse_figures_block, scan_figures
from src.agent.grounding.policies import ValidationResult

pytestmark = pytest.mark.unit


def _ledger(tmp_path: Path, entries):
    symbols = " ".join(symbol for _, _, _, rows in entries for symbol, _ in rows)
    ledger = GroundingLedger(run_dir=tmp_path, user_message=f"portfolio contribution report {symbols}")
    for call, block, period, rows in entries:
        payload = {"status": "ok", "data": {
            "ok": True, "status": "fresh", "version": "canonical_attribution_v2_income_capital_return",
            "scope": {"asset_type": block, "currency": "ARS"},
            "start_date": period[0], "end_date": period[1],
            "requested_start_date": period[0], "requested_end_date": period[1],
            "effective_start_date": period[0], "effective_end_date": period[1],
            "boundary_adjustments": [],
            "positions": [
                {"symbol": symbol, "tipo": block, "contribution_pct": value}
                for symbol, value in rows
            ],
        }
        }
        ledger.ingest_tool_result(
            tool_name="mcp_asistente_casa_consultar_attribution_cartera_scope",
            arguments={"asset_type": block, "period": "1r"}, result=json.dumps(payload),
            call_id=call, success=True,
        )
    return ledger


def _draft(symbol, value, block, period=("2026-10-06", "2026-10-07"), unit="pp"):
    return (
        "**Asunto: Informe diario de cierre de cartera**\n\n"
        f"> **Fecha de referencia:** cierre canónico completo del {int(period[1][8:10])} de octubre de {period[1][:4]}.\n\n"
        "## Qué explicó el resultado\n\n### Comportamiento por bloques\n\n"
        f"En {block}, principales contribuciones:\n\n"
        f"- **{symbol}:** contribución de **{value}** {unit} dentro del bloque.\n\n"
        "```figures\n```"
    )


def _repair(ledger, draft):
    rejected = ledger.validate_final_answer(draft)
    assert not rejected.valid
    assert all(i["code"] == "figure_undeclared" for i in rejected.issues)
    repaired = ledger.repair_undeclared_attribution(draft, rejected)
    if repaired is None:
        return None
    checked = ledger.revalidate(repaired)
    return repaired if checked.valid else None


def test_recovers_ypfd_negative_block_contribution(tmp_path):
    ledger = _ledger(tmp_path, [("call-ypfd|fc-1", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    repaired = _repair(ledger, _draft("YPFD", "-1.28", "ACCIONES", unit="puntos porcentuales"))
    assert repaired and "observed | canonical ACCIONES" in repaired
    assert "call-ypfd|fc-1::data.positions[0].contribution_pct" in repaired


def test_recovers_mu_positive_block_contribution(tmp_path):
    ledger = _ledger(tmp_path, [("call-mu|fc-2", "CEDEARS", ("2026-10-06", "2026-10-07"), [("MU", 0.210)])])
    repaired = _repair(ledger, _draft("MU", "+0.21", "CEDEARS", unit="puntos porcentuales"))
    assert repaired and "call-mu|fc-2::data.positions[0].contribution_pct" in repaired


def test_recovers_both_original_figures_in_one_correction(tmp_path):
    ledger = _ledger(tmp_path, [
        ("call-ypfd|fc-1", "ACCIONES", ("2026-10-06", "2026-10-07"),
         [(f"FILL{i}", 0.0) for i in range(12)] + [("YPFD", -1.276)]),
        ("call-mu|fc-2", "CEDEARS", ("2026-10-06", "2026-10-07"),
         [("MRNA", 0.272), ("MU", 0.210)]),
    ])
    # Persisted session 8f9fdc23906f used a single report close-date header,
    # followed by separate block headings and instrument-specific bullets.
    draft = (
        "**Asunto: Informe diario de cartera — último cierre canónico completo al 7 de octubre de 2026**\n\n"
        "> **Fecha de referencia:** el último cierre canónico completo disponible es el **7 de octubre de 2026**.\n\n"
        "## Qué explicó el resultado\n\n### Comportamiento por bloques\n\n"
        "En ACCIONES, la debilidad se concentró principalmente en energía:\n\n"
        "- **YPFD:** contribución de **-1.28** puntos porcentuales dentro del bloque.\n"
        "- PAMP y TGSU2 también fueron detractores relevantes.\n\n"
        "En CEDEARs:\n\n"
        "- MRNA y MU fueron las principales defensas. MU aportó **+0.21** puntos porcentuales dentro del bloque.\n\n"
        "```figures\n```"
    )
    repaired = _repair(ledger, draft)
    assert repaired
    assert "-1.28 | observed" in repaired
    assert "+0.21 | observed" in repaired
    assert "call-ypfd|fc-1::data.positions[12].contribution_pct" in repaired
    assert "call-mu|fc-2::data.positions[1].contribution_pct" in repaired


def test_all_and_block_values_are_not_interchangeable(tmp_path):
    ledger = _ledger(tmp_path, [
        ("block|fc-1", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)]),
        ("all|fc-2", "ALL", ("2026-10-06", "2026-10-07"), [("YPFD", -0.646)]),
    ])
    assert _repair(ledger, _draft("YPFD", "-0.646", "ACCIONES")) is None


def test_mismatched_position_block_heading_fails_closed(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    assert _repair(ledger, _draft("YPFD", "-1.28", "CEDEARS")) is None


@pytest.mark.parametrize(
    "value,unit",
    [("+1.276", "pp"), ("-1.276", "%"), ("-1.276", "percent"), ("-1.2", "pp")],
)
def test_wrong_sign_or_percent_unit_fails_closed(tmp_path, value, unit):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    assert _repair(ledger, _draft("YPFD", value, "ACCIONES", unit=unit)) is None


def test_duplicate_matching_calls_are_ambiguous(tmp_path):
    ledger = _ledger(tmp_path, [
        ("call-a|fc-a", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)]),
        ("call-b|fc-b", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)]),
    ])
    assert _repair(ledger, _draft("YPFD", "-1.276", "ACCIONES")) is None


def test_period_and_instrument_mismatch_fail_closed(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    assert _repair(ledger, _draft("YPFD", "-1.276", "ACCIONES", period=("2026-10-05", "2026-10-08"))) is None
    assert _repair(ledger, _draft("GGAL", "-1.276", "ACCIONES")) is None


def test_multiple_report_reference_periods_fail_closed(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    draft = _draft("YPFD", "-1.28", "ACCIONES")
    draft = draft.replace(
        "## Qué explicó el resultado",
        "> Fecha de referencia: cierre canónico completo del 8 de octubre de 2026.\n\n"
        "## Qué explicó el resultado",
    )
    assert _repair(ledger, draft) is None


def test_missing_structural_report_context_fails_closed(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    drafts = [
        "YPFD contributed -1.28 puntos porcentuales within ACCIONES.\n\n```figures\n```",
        "**Informe diario de cierre**\n\n## Qué explicó el resultado\n\n"
        "En ACCIONES, resultado:\nYPFD contribuyó -1.28 puntos porcentuales.\n\n```figures\n```",
        _draft("YPFD", "-1.28", "ACCIONES").replace("Informe diario de cierre de cartera", "Informe de cartera"),
    ]
    for draft in drafts:
        assert _repair(ledger, draft) is None


@pytest.mark.parametrize(
    ("tool", "field"),
    [
        ("portfolio_summary", "market_value_ars"),
        ("mcp_asistente_casa_consultar_performance_cartera_scope", "daily_return_pct"),
        ("portfolio_risk_xray", "volatility_pct"),
    ],
)
def test_valuation_return_and_risk_evidence_cannot_trigger_attribution_repair(
    tmp_path, tool, field
):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio contribution report YPFD")
    payload = {"data": {"positions": [{"symbol": "YPFD", field: -1.276}]}}
    ledger.ingest_tool_result(
        tool_name=tool, arguments={}, result=json.dumps(payload),
        call_id=f"{tool}-call", success=True,
    )
    draft = _draft("YPFD", "-1.28", "ACCIONES")
    figure = scan_figures(draft, parse_figures_block(draft))[0]
    issue = {"code": "figure_undeclared", "span": [figure.start, figure.end], "symbol": "YPFD"}
    rejected = ValidationResult(valid=False, issues=[issue])
    assert ledger.repair_undeclared_attribution(draft, rejected) is None


def test_coincident_values_from_different_period_calls_are_ambiguous(tmp_path):
    ledger = _ledger(tmp_path, [
        ("call-a|fc-a", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)]),
        ("call-b|fc-b", "ACCIONES", ("2026-10-05", "2026-10-07"), [("YPFD", -1.276)]),
    ])
    assert _repair(ledger, _draft("YPFD", "-1.28", "ACCIONES")) is None


def test_invented_value_and_predeclared_valid_figure_are_not_rewritten(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    assert _repair(ledger, _draft("YPFD", "-9.999", "ACCIONES")) is None
    declared = "YPFD contributed -1.276 pp within ACCIONES for 2026-10-06 to 2026-10-07.\n\n```figures\n-1.276 | observed | canonical contribution | call|fc::data.positions[0].contribution_pct\n```"
    result = ledger.validate_final_answer(declared)
    assert result.valid
    assert ledger.repair_undeclared_attribution(declared, result) is None
