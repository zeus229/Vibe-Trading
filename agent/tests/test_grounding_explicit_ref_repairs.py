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


def test_exact_aggregate_ref_stays_invalid_when_claim_is_symbol_scoped(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    aggregate = _record()
    ledger._evidence = [aggregate]
    figure = _percent_figure()

    records, metrics = ledger._referenced_one(
        "call_portfolio::data.concentration.top1_pct",
        "YPFD",
        figure,
    )

    assert records == []
    assert metrics == []
    assert ledger._aggregate_exact_ref_match(
        "call_portfolio::data.concentration.top1_pct", figure
    ) is True


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

    assert "valid field refs: call_acciones::data.volatility.annualized_vol" in line
    assert line.count("call_acciones::data.volatility.annualized_vol") == 1
    assert "keep the written value and replace only the incorrect ref" in line


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


def test_tool_field_candidates_keep_only_calls_matching_written_value(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="risk comparison")
    acciones = EvidenceRecord(
        call_id="call_acciones",
        tool="asistente_casa_portfolio_risk_xray",
        symbol=None,
        source="asistente_casa",
        timestamp=None,
        field="data.volatility.annualized_vol",
        value=35.72,
        status="observed",
        unit="ratio",
        identity_scope="aggregate",
    )
    cedears = EvidenceRecord(
        call_id="call_cedears",
        tool="asistente_casa_portfolio_risk_xray",
        symbol=None,
        source="asistente_casa",
        timestamp=None,
        field="data.volatility.annualized_vol",
        value=24.12,
        status="observed",
        unit="ratio",
        identity_scope="aggregate",
    )
    ledger._evidence = [acciones, cedears]

    candidates = ledger._tool_field_ref_candidates(
        "asistente_casa_portfolio_risk_xray::data.volatility.annualized_vol",
        None,
        _percent_figure(35.72),
    )

    assert candidates == ["call_acciones::data.volatility.annualized_vol"]


def test_unique_first_pass_candidate_is_prescriptive():
    line = _correction_line(
        {
            "code": "numeric_claim_conflict",
            "value": "35.72%",
            "role": "observed",
            "reason": "field_ref_needs_call_id",
            "source_tool_call_ids": [
                "asistente_casa_portfolio_risk_xray::data.volatility.annualized_vol"
            ],
            "ambiguous_sources": [
                "call_acciones::data.volatility.annualized_vol"
            ],
            "field_ref_candidates": [
                "call_acciones::data.volatility.annualized_vol"
            ],
        }
    )

    assert "matching exact call_id::field ref(s): call_acciones::data.volatility.annualized_vol" in line
    assert line.count("call_acciones::data.volatility.annualized_vol") == 1
    assert "keep the written value and replace only the incorrect ref" in line


def test_aggregate_scope_feedback_preserves_fail_closed_and_explains_rewrite(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    ledger._evidence = [_record()]
    figure = _percent_figure()

    assert ledger._aggregate_exact_ref_match(
        "call_portfolio::data.concentration.top1_pct", figure
    )
    assert not ledger._aggregate_exact_ref_match(
        "portfolio_summary::data.concentration.top1_pct", figure
    )


def test_compact_queue_surfaces_rejected_issues_beyond_detailed_cap(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    issues = [
        {
            "code": "numeric_claim_conflict",
            "value": f"{index}.00%",
            "role": "observed",
            "reason": "field_ref_needs_call_id",
            "source_tool_call_ids": ["tool::data.metric"],
            "field_ref_candidates": [f"call_{index}::data.metric"],
            **(
                {"aggregate_ref_candidate": f"call_{index}::data.metric"}
                if index == 29
                else {}
            ),
        }
        for index in range(30)
    ]
    from src.agent.grounding.policies import ValidationResult

    prompt = ledger.correction_prompt(
        ValidationResult(valid=False, issues=issues, released_text="", passed_figures=())
    )

    assert "There are 30 rejected issue(s) total" in prompt
    assert "call_29::data.metric" in prompt
    assert "aggregate=keep value/ref but rewrite as portfolio-level" in prompt
    assert "EVERY remaining issue below is also rejected" in prompt


def test_prompt_never_says_unlisted_rejected_figures_checked_clean(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    from src.agent.grounding.policies import ValidationResult

    issues = [
        {
            "code": "numeric_claim_conflict",
            "value": f"{index}.00%",
            "role": "observed",
            "reason": "not_in_referenced_call",
            "source_tool_call_ids": ["wrong::field"],
        }
        for index in range(25)
    ]
    result = ValidationResult(
        valid=False,
        issues=issues,
        released_text="",
        passed_figures=("1.23%",),
    )
    prompt = ledger.correction_prompt(result)

    assert "Every other measured figure in the draft checked clean" not in prompt
    assert "only the figures listed above need work" not in prompt
    assert "Do not infer that an unlisted figure passed" in prompt
