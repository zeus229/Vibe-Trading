"""Regressions for exact aggregate refs and correction ref repair hints."""

from pathlib import Path

from src.agent.grounding.evidence import EvidenceRecord
from src.agent.grounding.figures import Declaration, Figure
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


def test_undeclared_entity_value_gets_exact_ref_and_spatial_hint(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="benchmark comparison")
    ledger._evidence = [
        EvidenceRecord(
            call_id="call_spy",
            tool="asistente_casa_portfolio_performance",
            symbol="SPY",
            source="asistente_casa",
            timestamp=None,
            field="data.references.SPY_ARS.excess_return_pct",
            value=1.79,
            status="observed",
            unit="ratio",
            identity_scope="entity",
        )
    ]
    figure = _percent_figure(1.79)

    hint = ledger._undeclared_entity_repair_metadata(figure)

    assert hint == {
        "field_ref_candidates": [
            "call_spy::data.references.SPY_ARS.excess_return_pct"
        ],
        "entity_ref_symbol": "SPY",
    }


def test_undeclared_entity_hint_stays_empty_when_value_is_ambiguous(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="benchmark comparison")
    ledger._evidence = [
        EvidenceRecord(
            call_id="call_spy",
            tool="asistente_casa_portfolio_performance",
            symbol="SPY",
            source="asistente_casa",
            timestamp=None,
            field="data.references.SPY_ARS.excess_return_pct",
            value=1.79,
            status="observed",
            unit="ratio",
            identity_scope="entity",
        ),
        EvidenceRecord(
            call_id="call_other",
            tool="asistente_casa_portfolio_performance",
            symbol="OTHER",
            source="asistente_casa",
            timestamp=None,
            field="data.references.OTHER.excess_return_pct",
            value=1.79,
            status="observed",
            unit="ratio",
            identity_scope="entity",
        ),
    ]

    assert ledger._undeclared_entity_repair_metadata(_percent_figure(1.79)) == {}


def test_undeclared_entity_issue_carries_hint_into_correction(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="benchmark comparison")
    ledger._evidence = [
        EvidenceRecord(
            call_id="call_spy",
            tool="asistente_casa_portfolio_performance",
            symbol="SPY",
            source="asistente_casa",
            timestamp=None,
            field="data.references.SPY_ARS.excess_return_pct",
            value=1.79,
            status="observed",
            unit="ratio",
            identity_scope="entity",
        )
    ]
    issue = {
        "code": "figure_undeclared",
        "value": "+1.79",
        "role": None,
        "reason": "undeclared",
        "symbol": "CCL",
        "field_ref_candidates": [
            "call_spy::data.references.SPY_ARS.excess_return_pct"
        ],
        "entity_ref_symbol": "SPY",
    }

    line = _correction_line(issue)

    assert "valid field refs: call_spy::data.references.SPY_ARS.excess_return_pct" in line
    assert "scoped only to entity SPY" in line
    assert "do not mix another ticker, benchmark, or entity" in line


def test_exact_entity_ref_mismatch_surfaces_spatial_repair_metadata(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="benchmark comparison")
    ledger._evidence = [
        EvidenceRecord(
            call_id="call_spy",
            tool="asistente_casa_portfolio_performance",
            symbol="SPY",
            source="asistente_casa",
            timestamp=None,
            field="data.references.SPY_ARS.excess_return_pct",
            value=1.79,
            status="observed",
            unit="ratio",
            identity_scope="entity",
        )
    ]
    figure = _percent_figure(1.79)
    declaration = Declaration(
        index=0,
        value_text=figure.text,
        value=figure.value,
        percent=True,
        role="observed",
        note="portfolio excess return versus benchmark",
        ref="call_spy::data.references.SPY_ARS.excess_return_pct",
    )

    issues = ledger._check_observed(figure, declaration, "CCL", ledger._evidence)

    assert len(issues) == 1
    assert issues[0]["reason"] == "entity_ref_needs_scoped_claim"
    assert issues[0]["entity_ref_symbol"] == "SPY"
    assert issues[0]["source_tool_call_ids"] == [
        "call_spy::data.references.SPY_ARS.excess_return_pct"
    ]


def test_exact_entity_ref_spatial_repair_keeps_gate_fail_closed(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="benchmark comparison")
    ledger._evidence = [
        EvidenceRecord(
            call_id="call_spy",
            tool="asistente_casa_portfolio_performance",
            symbol="SPY",
            source="asistente_casa",
            timestamp=None,
            field="data.references.SPY_ARS.excess_return_pct",
            value=1.79,
            status="observed",
            unit="ratio",
            identity_scope="entity",
        )
    ]
    figure = _percent_figure(1.79)

    wrong_records, _ = ledger._referenced_one(
        "call_spy::data.references.SPY_ARS.excess_return_pct",
        "CCL",
        figure,
    )
    right_records, _ = ledger._referenced_one(
        "call_spy::data.references.SPY_ARS.excess_return_pct",
        "SPY",
        figure,
    )

    assert wrong_records == []
    assert len(right_records) == 1
    assert right_records[0].symbol == "SPY"


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


def test_detailed_entity_ref_mismatch_requires_spatial_separation():
    line = _correction_line(
        {
            "code": "numeric_claim_conflict",
            "value": "+1.79",
            "role": "observed",
            "reason": "entity_ref_needs_scoped_claim",
            "symbol": "CCL",
            "source_tool_call_ids": [
                "call_spy::data.references.SPY_ARS.excess_return_pct"
            ],
            "entity_ref_symbol": "SPY",
        }
    )

    assert "scoped only to entity SPY" in line
    assert "do not mix another ticker, benchmark, or entity" in line
    assert "separate cells, rows, or sentences" in line


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


def test_multiple_matching_calls_are_exposed_as_proven_repair_options(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio value")
    first = EvidenceRecord(
        call_id="call_snapshot_a",
        tool="portfolio_summary",
        symbol=None,
        source="asistente_casa",
        timestamp=None,
        field="data.total_value",
        value=221963026.08,
        status="observed",
        unit="money",
        identity_scope="aggregate",
    )
    second = EvidenceRecord(
        call_id="call_snapshot_b",
        tool="portfolio_summary",
        symbol=None,
        source="asistente_casa",
        timestamp=None,
        field="data.total_value",
        value=221963026.08,
        status="observed",
        unit="money",
        identity_scope="aggregate",
    )
    ledger._evidence = [first, second]
    figure = Figure(
        text="221963026.08",
        value=221963026.08,
        percent=False,
        start=0,
        end=12,
        line=0,
        shape="measured",
        digits="221963026.08",
        currency=True,
    )

    candidates = ledger._tool_field_ref_candidates(
        "portfolio_summary::data.total_value",
        None,
        figure,
    )
    metadata = ledger._exact_repair_metadata(candidates, figure, None)

    assert candidates == [
        "call_snapshot_b::data.total_value",
        "call_snapshot_a::data.total_value",
    ]
    assert metadata["proven_ref_repair_candidates"] == candidates
    assert "exact_ref_repair_candidate" not in metadata
    assert metadata["aggregate_ref_candidates"] == candidates


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


def test_detailed_aggregate_candidate_requires_spatial_separation():
    line = _correction_line(
        {
            "code": "numeric_claim_conflict",
            "value": "20.18%",
            "role": "observed",
            "reason": "field_ref_needs_call_id",
            "source_tool_call_ids": [
                "portfolio_summary::data.concentration.top1_pct"
            ],
            "field_ref_candidates": [
                "call_portfolio::data.concentration.top1_pct"
            ],
            "aggregate_ref_candidate": (
                "call_portfolio::data.concentration.top1_pct"
            ),
        }
    )

    assert "keep the value/ref aggregate and rewrite it as aggregate/unscoped" in line
    assert (
        "do not mention any ticker or instrument anywhere on the same line "
        "or in the same paragraph" in line
    )
    assert "move that comparison to a separate paragraph or table row" in line


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
    assert "aggregate=keep the value/ref aggregate and rewrite it as aggregate/unscoped" in prompt
    assert "do not mention any ticker or instrument anywhere on the same line or in the same paragraph" in prompt
    assert "EVERY remaining issue below is also rejected" in prompt


def test_compact_entity_ref_mismatch_requires_spatial_separation(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="benchmark comparison")
    issues = [
        {
            "code": "numeric_claim_conflict",
            "value": f"{index}.00%",
            "role": "observed",
            "reason": "not_in_referenced_call",
            "source_tool_call_ids": ["wrong::field"],
        }
        for index in range(24)
    ]
    issues.append(
        {
            "code": "numeric_claim_conflict",
            "value": "+1.79",
            "role": "observed",
            "reason": "entity_ref_needs_scoped_claim",
            "symbol": "CCL",
            "source_tool_call_ids": [
                "call_spy::data.references.SPY_ARS.excess_return_pct"
            ],
            "entity_ref_symbol": "SPY",
        }
    )
    from src.agent.grounding.policies import ValidationResult

    prompt = ledger.correction_prompt(
        ValidationResult(valid=False, issues=issues, released_text="", passed_figures=())
    )

    assert "entity=keep the exact value/ref" in prompt
    assert "scoped only to entity SPY" in prompt
    assert "do not mix another ticker, benchmark, or entity" in prompt


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


def test_unscoped_aggregate_candidate_is_flagged_for_first_pass_correction(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    ledger._evidence = [_record()]
    figure = _percent_figure()
    declaration = Declaration(
        index=0,
        value_text=figure.text,
        value=figure.value,
        percent=True,
        role="observed",
        note="Top 1",
        ref="portfolio_summary::data.concentration.top1_pct",
    )

    issues = ledger._check_observed(figure, declaration, None, ledger._evidence)

    assert len(issues) == 1
    assert issues[0]["reason"] == "field_ref_needs_call_id"
    assert issues[0]["field_ref_candidates"] == [
        "call_portfolio::data.concentration.top1_pct"
    ]
    assert issues[0]["aggregate_ref_candidate"] == (
        "call_portfolio::data.concentration.top1_pct"
    )


def test_unknown_alias_candidates_keep_only_matching_call(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    ledger._evidence = [
        _record(value=20.18),
        EvidenceRecord(
            call_id="call_other",
            tool="portfolio_summary",
            symbol=None,
            source="asistente_casa",
            timestamp=None,
            field="data.concentration.top1_pct",
            value=19.75,
            status="observed",
            unit="ratio",
            identity_scope="aggregate",
        ),
    ]

    candidates = ledger._unknown_call_field_ref_candidates(
        "ac_perf_1r::data.concentration.top1_pct",
        None,
        _percent_figure(20.18),
    )

    assert candidates == ["call_portfolio::data.concentration.top1_pct"]


def test_unknown_alias_propagates_aggregate_repair_metadata(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    ledger._evidence = [_record(value=20.18)]
    figure = _percent_figure(20.18)
    declaration = Declaration(
        index=0,
        value_text=figure.text,
        value=figure.value,
        percent=True,
        role="observed",
        note="Top 1",
        ref="ac_perf_1r::data.concentration.top1_pct",
    )

    issues = ledger._check_observed(figure, declaration, "YPFD", ledger._evidence)

    assert len(issues) == 1
    assert issues[0]["reason"] == "unknown_call_id"
    assert issues[0]["field_ref_candidates"] == [
        "call_portfolio::data.concentration.top1_pct"
    ]
    assert issues[0]["aggregate_ref_candidate"] == (
        "call_portfolio::data.concentration.top1_pct"
    )


def test_session_scope_propagates_aggregate_repair_metadata(tmp_path: Path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="portfolio report")
    ledger._evidence = [_record(value=20.18)]
    figure = _percent_figure(20.18)
    declaration = Declaration(
        index=0,
        value_text=figure.text,
        value=figure.value,
        percent=True,
        role="observed",
        note="Top 1",
        ref="run_scope::alias.data.concentration.top1_pct",
    )
    original_artifact_scope = ledger._artifact_scope

    def artifact_scope(key: str):
        if key == "run_scope":
            return [object()]
        return original_artifact_scope(key)

    ledger._artifact_scope = artifact_scope  # type: ignore[method-assign]

    issues = ledger._check_observed(figure, declaration, "YPFD", ledger._evidence)

    assert len(issues) == 1
    assert issues[0]["reason"] == "session_scope_needs_call_id"
    assert issues[0]["field_ref_candidates"] == [
        "call_portfolio::data.concentration.top1_pct"
    ]
    assert issues[0]["aggregate_ref_candidate"] == (
        "call_portfolio::data.concentration.top1_pct"
    )
