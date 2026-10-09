from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit


def _ledger(tmp_path: Path, entries):
    symbols = " ".join(symbol for _, _, _, rows in entries for symbol, _ in rows)
    ledger = GroundingLedger(run_dir=tmp_path, user_message=f"portfolio contribution report {symbols}")
    for call, block, period, rows in entries:
        payload = {
            "start_date": period[0], "end_date": period[1], "asset_type": block,
            "data": {"positions": [
                {"symbol": symbol, "contribution_pct": value} for symbol, value in rows
            ]},
        }
        ledger.ingest_tool_result(
            tool_name="portfolio_attribution", arguments={}, result=json.dumps(payload),
            call_id=call, success=True,
        )
    return ledger


def _draft(symbol, value, block, period=("2026-10-06", "2026-10-07"), unit="pp"):
    return (
        f"{symbol} contributed {value} {unit} within {block} for "
        f"{period[0]} to {period[1]}.\n\n```figures\n```"
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
    repaired = _repair(ledger, "YPFD: contribución −1.276 puntos porcentuales dentro del bloque ACCIONES para 2026-10-06 a 2026-10-07.\n\n```figures\n```")
    assert repaired and "observed | canonical ACCIONES" in repaired
    assert "call-ypfd|fc-1::data.positions[0].contribution_pct" in repaired


def test_recovers_mu_positive_block_contribution(tmp_path):
    ledger = _ledger(tmp_path, [("call-mu|fc-2", "CEDEARS", ("2026-10-06", "2026-10-07"), [("MU", 0.210)])])
    repaired = _repair(ledger, "MU aportó +0.210 puntos porcentuales dentro del bloque CEDEARS para 2026-10-06 a 2026-10-07.\n\n```figures\n```")
    assert repaired and "call-mu|fc-2::data.positions[0].contribution_pct" in repaired


def test_recovers_both_original_figures_in_one_correction(tmp_path):
    ledger = _ledger(tmp_path, [
        ("call-ypfd|fc-1", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)]),
        ("call-mu|fc-2", "CEDEARS", ("2026-10-06", "2026-10-07"), [("MU", 0.210)]),
    ])
    draft = (
        "YPFD contribuyó −1.276 puntos porcentuales dentro del bloque ACCIONES para 2026-10-06 a 2026-10-07.\n"
        "MU aportó +0.210 puntos porcentuales dentro del bloque CEDEARS para 2026-10-06 a 2026-10-07.\n\n"
        "```figures\n```"
    )
    repaired = _repair(ledger, draft)
    assert repaired
    assert "call-ypfd|fc-1::data.positions[0].contribution_pct" in repaired
    assert "call-mu|fc-2::data.positions[0].contribution_pct" in repaired


def test_all_and_block_values_are_not_interchangeable(tmp_path):
    ledger = _ledger(tmp_path, [
        ("block|fc-1", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)]),
        ("all|fc-2", "ALL", ("2026-10-06", "2026-10-07"), [("YPFD", -0.646)]),
    ])
    assert _repair(ledger, _draft("YPFD", "-0.646", "ACCIONES")) is None


@pytest.mark.parametrize("value,unit", [("+1.276", "pp"), ("-1.276", "%"), ("-1.276", "percent")])
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
    assert _repair(ledger, _draft("YPFD", "-1.276", "ACCIONES", period=("2026-10-05", "2026-10-07"))) is None
    assert _repair(ledger, _draft("GGAL", "-1.276", "ACCIONES")) is None


def test_invented_value_and_predeclared_valid_figure_are_not_rewritten(tmp_path):
    ledger = _ledger(tmp_path, [("call|fc", "ACCIONES", ("2026-10-06", "2026-10-07"), [("YPFD", -1.276)])])
    assert _repair(ledger, _draft("YPFD", "-9.999", "ACCIONES")) is None
    declared = "YPFD contributed -1.276 pp within ACCIONES for 2026-10-06 to 2026-10-07.\n\n```figures\n-1.276 | observed | canonical contribution | call|fc::data.positions[0].contribution_pct\n```"
    result = ledger.validate_final_answer(declared)
    assert result.valid
    assert ledger.repair_undeclared_attribution(declared, result) is None
