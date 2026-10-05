"""Regressions for exact aggregate refs and correction ref repair hints."""

from pathlib import Path

from src.agent.grounding.evidence import EvidenceRecord
from src.agent.grounding.figures import Figure
from src.agent.grounding.ledger import GroundingLedger
from src.agent.grounding.release import _correction_line


def _record(*, symbol=None, identity_scope="aggregate", value=20.3):
    return EvidenceRecord(
        call_id="call_portfolio",
        tool="portfolio_summary",
        symbol=symbol,
        source="asistente_casa",
        timestamp=None,
        field="data.concentration.top1_pct",
        value=value,
        status="observed",
        unit="ratio",
        identity_scope=identity_scope,
    )


def _percent_figure(value=20.3):
    return Figure(
        text=f"{value:.2f}%",
        value=value,
        percent=True,
        start=0,
        end=6,
        line=0,
        shape="measured",
        digits=f"{value:.2f}",
    )


def test_exact_field_ref_keeps_aggregate_evidence_despite_neighbor_symbol(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    aggregate = _record()
    ledger._evidence = [aggregate]

    records, metrics = ledger._referenced_one(
        "call_portfolio::data.concentration.top1_pct",
        "YPFD",
        _percent_figure(),
    )

    assert records == [aggregate]
    assert metrics == []


def test_exact_field_ref_still_rejects_other_entity(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    ledger._evidence = [_record(symbol="JPM", identity_scope="entity")]

    records, metrics = ledger._referenced_one(
        "call_portfolio::data.concentration.top1_pct",
        "YPFD",
        _percent_figure(),
    )

    assert records == []
    assert metrics == []


def test_unique_wrong_call_candidate_tells_correction_to_replace_only_ref():
    line = _correction_line(
        {
            "code": "numeric_claim_conflict",
            "value": "35.72%",
            "role": "observed",
            "reason": "not_in_referenced_call",
            "source_tool_call_ids": [
                "call_cedears::data.volatility.annualized_vol"
            ],
            "field_ref_candidates": [
                "call_acciones::data.volatility.annualized_vol"
            ],
        }
    )

    assert "written value matches this exact session ref" in line
    assert "call_acciones::data.volatility.annualized_vol" in line
    assert "keep the value and replace only the incorrect ref" in line


def test_multiple_candidates_remain_non_prescriptive():
    line = _correction_line(
        {
            "code": "numeric_claim_conflict",
            "value": "35.72%",
            "role": "observed",
            "reason": "not_in_referenced_call",
            "source_tool_call_ids": ["wrong::field"],
            "field_ref_candidates": ["call_a::field", "call_b::field"],
        }
    )

    assert "valid field refs: call_a::field, call_b::field" in line
    assert "replace only the incorrect ref" not in line
