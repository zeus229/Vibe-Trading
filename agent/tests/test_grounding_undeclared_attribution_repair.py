from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.figures import parse_figures_block, scan_figures
from src.agent.grounding.policies import ValidationResult
from src.agent.loop import AgentLoop
from src.agent.tools import BaseTool, ToolRegistry
from src.agent.trace import TraceWriter

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


def test_replaces_same_ref_percentage_declarations_only_for_explicit_pp_claims(tmp_path):
    ledger = _ledger(tmp_path, [
        ("call-ypfd|fc-1", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)]),
        ("call-mu|fc-2", "CEDEARS", ("2026-10-06", "2026-10-07"), [("MRNA", 0.272), ("MU", 0.210)]),
    ])
    draft = _draft("YPFD", "-1.28", "ACCIONES", unit="puntos porcentuales").replace(
        "```figures\n```",
        "En CEDEARS, principales contribuciones:\n\n"
        "- **MRNA y MU** fueron las principales defensas. MU aportó **+0.21** puntos porcentuales dentro del bloque.\n\n"
        "```figures\n"
        "-1.28% | observed | stale percent unit | call-ypfd|fc-1::data.positions[0].contribution_pct\n"
        "0.21% | observed | stale percent unit | call-mu|fc-2::data.positions[1].contribution_pct\n```",
    )
    rejected = ledger.validate_final_answer(draft)
    assert [issue["code"] for issue in rejected.issues] == ["figure_undeclared", "figure_undeclared"]
    repaired = ledger.repair_undeclared_attribution(draft, rejected)
    assert repaired
    parsed = parse_figures_block(repaired)
    by_ref = {item.ref: item for item in parsed.declarations}
    assert by_ref["call-ypfd|fc-1::data.positions[0].contribution_pct"].value_text == "-1.28"
    assert not by_ref["call-ypfd|fc-1::data.positions[0].contribution_pct"].percent
    assert by_ref["call-mu|fc-2::data.positions[1].contribution_pct"].value_text == "0.21"
    assert not by_ref["call-mu|fc-2::data.positions[1].contribution_pct"].percent
    assert sum(item.ref == "call-ypfd|fc-1::data.positions[0].contribution_pct" for item in parsed.declarations) == 1
    assert sum(item.ref == "call-mu|fc-2::data.positions[1].contribution_pct" for item in parsed.declarations) == 1
    assert ledger.revalidate(repaired).valid


def test_does_not_rewrite_percent_claims_or_correct_point_declarations(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    ref = "call|fc::data.positions[0].contribution_pct"
    percent_claim = _draft("YPFD", "-1.28", "ACCIONES", unit="%")
    percent_claim = percent_claim.replace(
        "```figures\n```", f"```figures\n-1.28% | observed | percent claim | {ref}\n```"
    )
    percent_validation = ledger.validate_final_answer(percent_claim)
    assert ledger.repair_undeclared_attribution(percent_claim, percent_validation) is None
    assert f"-1.28% | observed | percent claim | {ref}" in percent_claim

    point_claim = _draft("YPFD", "-1.28", "ACCIONES", unit="puntos porcentuales")
    point_claim = point_claim.replace(
        "```figures\n```", f"```figures\n-1.28 | observed | canonical contribution pp | {ref}\n```"
    )
    point_validation = ledger.validate_final_answer(point_claim)
    assert point_validation.valid
    assert ledger.repair_undeclared_attribution(point_claim, point_validation) is None


def test_wrong_percent_declaration_with_wrong_value_or_sign_stays_fail_closed(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    ref = "call|fc::data.positions[0].contribution_pct"
    for declaration in (
        f"1.28% | observed | wrong sign | {ref}",
        f"-1.80% | observed | wrong value | {ref}",
        f"-1.28% | derived | wrong role | {ref}",
    ):
        draft = _draft("YPFD", "-1.28", "ACCIONES", unit="puntos porcentuales")
        draft = draft.replace("```figures\n```", f"```figures\n{declaration}\n```")
        rejected = ledger.validate_final_answer(draft)
        assert [issue["code"] for issue in rejected.issues] == ["figure_undeclared"]
        assert ledger.repair_undeclared_attribution(draft, rejected) is None


def test_wrong_percent_declaration_without_explicit_pp_unit_stays_fail_closed(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    ref = "call|fc::data.positions[0].contribution_pct"
    draft = _draft("YPFD", "-1.28", "ACCIONES", unit="")
    draft = draft.replace("```figures\n```", f"```figures\n-1.28% | observed | ambiguous unit | {ref}\n```")
    rejected = ledger.validate_final_answer(draft)
    assert [issue["code"] for issue in rejected.issues] == ["figure_undeclared"]
    assert ledger.repair_undeclared_attribution(draft, rejected) is None


class _AttributionTool(BaseTool):
    name = "mcp_asistente_casa_consultar_attribution_cartera_scope"
    description = "Read-only canonical portfolio attribution fixture."
    parameters = {
        "type": "object",
        "properties": {"asset_type": {"type": "string"}, "period": {"type": "string"}},
        "required": ["asset_type", "period"],
    }
    repeatable = True

    def execute(self, *, asset_type: str, period: str, **kwargs) -> str:
        rows = {"ACCIONES": [(f"FILL{i}", 0.0) for i in range(12)] + [("YPFD", -1.276)],
                "CEDEARS": [("MRNA", 0.272), ("MU", 0.210)]}[asset_type]
        return json.dumps({"status": "ok", "data": {
            "ok": True, "status": "fresh", "version": "canonical_attribution_v2_income_capital_return",
            "scope": {"asset_type": asset_type, "currency": "ARS"},
            "start_date": "2026-10-06", "end_date": "2026-10-07",
            "requested_start_date": "2026-10-06", "requested_end_date": "2026-10-07",
            "effective_start_date": "2026-10-06", "effective_end_date": "2026-10-07",
            "boundary_adjustments": [],
            "positions": [{"symbol": symbol, "tipo": asset_type, "contribution_pct": value}
                          for symbol, value in rows],
        }})


class _AttributionRunLLM:
    def __init__(self):
        self.responses = [
            {"id": "ypfd-call", "asset_type": "ACCIONES", "period": "1r"},
            {"id": "mu-call", "asset_type": "CEDEARS", "period": "1r"},
        ]
        self.calls = 0

    def stream_chat(self, messages, tools=None, on_text_chunk=None, **kwargs):
        from types import SimpleNamespace

        self.calls += 1
        if self.responses:
            call = self.responses.pop(0)
            return SimpleNamespace(
                has_tool_calls=True,
                tool_calls=[SimpleNamespace(
                    id=call["id"], name=_AttributionTool.name,
                    arguments={"asset_type": call["asset_type"], "period": call["period"]},
                )], content="", reasoning_content=None,
            )
        draft = (
            "**Asunto: Informe diario de cartera — último cierre canónico completo al 7 de octubre de 2026**\n\n"
            "> **Fecha de referencia:** el último cierre canónico completo disponible es el **7 de octubre de 2026**.\n\n"
            "## Qué explicó el resultado\n\n### Comportamiento por bloques\n\n"
            "En ACCIONES, la debilidad se concentró principalmente en energía:\n\n"
            "- **YPFD:** contribución de **-1.28** puntos porcentuales dentro del bloque.\n\n"
            "En CEDEARs:\n\n"
            "- MRNA y MU fueron las principales defensas. MU aportó **+0.21** puntos porcentuales dentro del bloque.\n\n"
            "```figures\n"
            "-1.28% | observed | wrong unit for YPFD contribution | ypfd-call::data.positions[12].contribution_pct\n"
            "0.21% | observed | wrong unit for MU contribution | mu-call::data.positions[1].contribution_pct\n```"
        )
        if on_text_chunk:
            on_text_chunk(draft)
        return SimpleNamespace(has_tool_calls=False, tool_calls=[], content=draft, reasoning_content=None)

    def chat(self, messages, **kwargs):
        raise AssertionError("the deterministic release replay must not call chat")


def test_loop_releases_original_report_after_observe_repair_and_revalidation(tmp_path, monkeypatch):
    validated = []
    original_revalidate = GroundingLedger.revalidate

    def capture_revalidation(self, content):
        result = original_revalidate(self, content)
        validated.append((content, result.valid))
        return result

    monkeypatch.setattr(GroundingLedger, "revalidate", capture_revalidation)
    registry = ToolRegistry()
    registry.register(_AttributionTool())
    agent = AgentLoop(registry=registry, llm=_AttributionRunLLM(), max_iterations=8)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    agent.memory.run_dir = str(run_dir)

    result = agent.run("Informe diario de contribuciones de cartera para el último cierre canónico")

    assert result["status"] == "success"
    assert "YPFD" in result["content"] and "-1.28" in result["content"]
    assert "MU" in result["content"] and "+0.21" in result["content"]
    assert "omitted※" not in result["content"]
    trace = TraceWriter.read(run_dir)
    repairs = [event for event in trace if event.get("type") == "answer_repaired"]
    assert len(repairs) == 1
    assert repairs[0]["repair"] == "figure_undeclared_canonical_attribution"
    assert any(
        is_valid
        and "ypfd-call::data.positions[12].contribution_pct" in repaired
        and "mu-call::data.positions[1].contribution_pct" in repaired
        for repaired, is_valid in validated
    )


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
