"""Regression probes for exact canonical percentage-point operand provenance.

These fixtures intentionally exercise Asistente Casa-shaped MCP evidence. They
must fail closed on scale ambiguity; market-data behaviour is covered upstream.
"""
import json

import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit


def _verdict(tmp_path, value, expression, refs, payload):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analyze my portfolio risk")
    ledger.ingest_tool_result(
        tool_name="asistente_casa_portfolio_risk_xray",
        arguments={},
        result=json.dumps(payload),
        call_id="risk",
        success=True,
    )
    return ledger.validate_final_answer(
        f"Portfolio concentration spread: {value}.\n\n"
        f"```figures\n{value} | derived | {expression} | {refs}\n```"
    )


def _payload():
    return {
        "data": {
            "snapshot_id": "synthetic-snapshot",
            "top1_pct": 20.36,
            "top5_pct": 61.56,
        }
    }


def test_exact_canonical_pct_refs_read_as_points(tmp_path):
    result = _verdict(
        tmp_path, "41.2%", "61.56 - 20.36",
        "risk::data.top5_pct; risk::data.top1_pct", _payload(),
    )
    assert result.valid, result.issues


def test_exact_canonical_pct_refs_reject_100x(tmp_path):
    result = _verdict(
        tmp_path, "4120%", "61.56 - 20.36",
        "risk::data.top5_pct; risk::data.top1_pct", _payload(),
    )
    assert not result.valid
    assert any(i.get("reason") == "derivation_result_mismatch" for i in result.issues)


def test_missing_canonical_pct_ref_is_not_substituted(tmp_path):
    result = _verdict(
        tmp_path, "41.2%", "61.56 - 20.36",
        "risk::data.top5_pct; risk::data.ghost_pct", _payload(),
    )
    assert not result.valid


def test_canonical_fraction_field_keeps_fraction_scale(tmp_path):
    data = {"data": {"snapshot_id": "synthetic-snapshot",
                      "start_value": 100.0, "end_value": 112.4}}
    result = _verdict(
        tmp_path, "12.4%", "(112.4 - 100.0) / 100.0",
        "risk::data.start_value; risk::data.end_value", data,
    )
    assert result.valid, result.issues
