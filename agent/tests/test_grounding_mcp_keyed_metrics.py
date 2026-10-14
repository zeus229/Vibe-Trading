
"""Generic regressions for keyed structured metrics and correction provenance."""
from __future__ import annotations

import json
from pathlib import Path

from src.agent.grounding.evidence import _metric_kind_for_path
from src.agent.grounding.ledger import GroundingLedger


SYMBOLS = tuple(f"SYMA{chr(65 + index)}" for index in range(13))
CALL_ID = "call_fixture_risk|fc_synthetic_20261014"
TOOL = "mcp_asistente_casa_consultar_riesgo_acciones_canonico"
VOL = 35.35


def _payload() -> dict:
    contribution = {symbol: float(index + 1) / 7 for index, symbol in enumerate(SYMBOLS)}
    matrix = {
        row: {
            column: 1.0 if row == column else round(0.1 + (i + j) / 100, 4)
            for j, column in enumerate(SYMBOLS)
        }
        for i, row in enumerate(SYMBOLS)
    }
    return {
        "data": {
            "status": "complete",
            "window_returns": 252,
            "portfolio_volatility_pct": VOL,
            "risk_contribution_pct": contribution,
            "correlation_matrix": matrix,
        }
    }


def _ref(path: str, *, real_call: bool = True) -> str:
    return f"{CALL_ID if real_call else TOOL}::{path}"


def _draft(*, real_refs: bool, changed: tuple[str, float] | None = None) -> str:
    payload = _payload()["data"]
    lines = ["Portfolio volatility: 35.35%.", "Risk contributions:"]
    declarations = [
        f"35.35% | observed | annualized volatility | {_ref('data.portfolio_volatility_pct', real_call=real_refs)}"
    ]
    for symbol, value in payload["risk_contribution_pct"].items():
        shown = value
        if changed and changed[0] == f"contribution:{symbol}":
            shown = changed[1]
        lines.append(f"{symbol}: {shown:.6g}%")
        declarations.append(
            f"{shown:.6g}% | observed | risk contribution | "
            f"{_ref(f'data.risk_contribution_pct.{symbol}', real_call=real_refs)}"
        )
    lines.append("Correlation matrix:")
    lines.append("| Symbol | " + " | ".join(SYMBOLS) + " |")
    lines.append("| --- | " + " | ".join("---:" for _ in SYMBOLS) + " |")
    for row in SYMBOLS:
        cells = []
        for column in SYMBOLS:
            value = payload["correlation_matrix"][row][column]
            shown = value
            if changed and changed[0] == f"correlation:{row}:{column}":
                shown = changed[1]
            cells.append(f"{shown:.4f}")
            declarations.append(
                f"{shown:.4f} | observed | correlation | "
                f"{_ref(f'data.correlation_matrix.{row}.{column}', real_call=real_refs)}"
            )
        lines.append("| " + row + " | " + " | ".join(cells) + " |")
    lines.append("\n```figures\n" + "\n".join(declarations) + "\n```")
    return "\n".join(lines)


def _ledger(tmp_path: Path, payload: dict | None = None) -> GroundingLedger:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Portfolio risk report")
    ledger.ingest_tool_result(
        tool_name=TOOL,
        arguments={"window": 252},
        result=json.dumps(payload or _payload()),
        call_id=CALL_ID,
        success=True,
    )
    return ledger


def test_keyed_contribution_and_matrix_paths_get_their_metric_kind():
    assert _metric_kind_for_path("data.risk_contribution_pct.SYMAA") == "contribution"
    assert _metric_kind_for_path("data.correlation_matrix.SYMAA.SYMAB") == "correlation"


def test_full_numeric_response_uses_exact_real_call_refs_and_validates(tmp_path: Path):
    ledger = _ledger(tmp_path)
    answer = _draft(real_refs=True)
    result = ledger.validate_final_answer(answer)
    assert result.valid, result.issues
    records = ledger._evidence
    assert sum(record.field.startswith("data.risk_contribution_pct.") for record in records) == 13
    assert sum(record.field.startswith("data.correlation_matrix.") for record in records) == 169
    for symbol in SYMBOLS:
        record = next(record for record in records if record.field == f"data.risk_contribution_pct.{symbol}")
        assert record.symbol == symbol and record.identity_scope == "entity"
    assert f"{CALL_ID}::data.portfolio_volatility_pct" in answer


def test_bad_tool_name_is_repaired_with_exact_call_id_and_contract_blocks_omission(tmp_path: Path):
    ledger = _ledger(tmp_path)
    answer = _draft(real_refs=False)
    rejected = ledger.validate_final_answer(answer)
    assert not rejected.valid
    assert all(issue["reason"] == "field_ref_needs_call_id" for issue in rejected.issues)

    prompt = ledger.correction_prompt(rejected)
    assert f"{CALL_ID}::data.portfolio_volatility_pct" in prompt
    assert f"{CALL_ID}::data.risk_contribution_pct.SYMAA" in prompt
    assert f"{CALL_ID}::data.correlation_matrix.SYMAA.SYMAB" in prompt

    contract = ledger.correction_contract(rejected)
    assert len(contract.required_figures) == 183
    assert len(ledger.correction_contract_missing("Qualitative summary only.", contract)) == 183
    corrected = _draft(real_refs=True)
    accepted = ledger.revalidate(corrected)
    assert accepted.valid, accepted.issues
    assert ledger.correction_contract_missing(corrected, contract) == ()


def test_undeclared_complete_matrix_gets_exact_refs_but_still_requires_correction(tmp_path: Path):
    ledger = _ledger(tmp_path)
    answer = _draft(real_refs=True).split("```figures", 1)[0] + "```figures\n```"
    rejected = ledger.validate_final_answer(answer)
    assert not rejected.valid
    assert len(rejected.issues) == 183
    prompt = ledger.correction_prompt(rejected)
    assert f"{CALL_ID}::data.risk_contribution_pct.SYMAA" in prompt
    assert f"{CALL_ID}::data.correlation_matrix.SYMAA.SYMAB" in prompt
    contract = ledger.correction_contract(rejected)
    assert len(contract.required_figures) == 183
    assert len(ledger.correction_contract_missing("Qualitative summary only.", contract)) == 183
    corrected = _draft(real_refs=True)
    assert ledger.revalidate(corrected).valid
    assert ledger.correction_contract_missing(corrected, contract) == ()


def test_changed_metric_value_does_not_get_a_proven_repair_or_pass(tmp_path: Path):
    ledger = _ledger(tmp_path)
    answer = _draft(
        real_refs=True,
        changed=("contribution:SYMAA", 88.0),
    )
    result = ledger.validate_final_answer(answer)
    assert not result.valid
    assert all(not issue.get("exact_ref_repair_candidate") for issue in result.issues)
    assert any(issue["value"] == "88%" for issue in result.issues)


def test_changed_matrix_cell_does_not_get_a_proven_repair_or_pass(tmp_path: Path):
    ledger = _ledger(tmp_path)
    answer = _draft(real_refs=True, changed=("correlation:SYMAA:SYMAB", 0.99))
    result = ledger.validate_final_answer(answer)
    assert not result.valid
    assert any(issue["value"] == "0.9900" for issue in result.issues)


def test_missing_matrix_leaf_has_no_candidate_and_fails_closed(tmp_path: Path):
    payload = _payload()
    del payload["data"]["correlation_matrix"]["SYMAA"]["SYMAB"]
    ledger = _ledger(tmp_path, payload)
    result = ledger.validate_final_answer(_draft(real_refs=True))
    assert not result.valid
    assert any(issue["value"] == "0.1100" for issue in result.issues)
    assert all(
        "data.correlation_matrix.SYMAA.SYMAB" not in str(issue.get("exact_ref_repair_candidate", ""))
        for issue in result.issues
    )


def test_mcp_adapter_namespace_keeps_percent_points_and_exact_ref_requirement(tmp_path: Path):
    ledger = _ledger(tmp_path)
    exact = f"{CALL_ID}::data.portfolio_volatility_pct"
    answer = (
        "The displayed volatility is 35.35%.\n\n"
        f"```figures\n35.35% | derived | 35.35 * 1 | {exact}\n```"
    )
    result = ledger.validate_final_answer(answer)
    assert result.valid, result.issues

    wrong_call = answer.replace(CALL_ID, "unknown_call")
    rejected = ledger.validate_final_answer(wrong_call)
    assert not rejected.valid
    assert any(issue["reason"] == "no_evidence" for issue in rejected.issues)
