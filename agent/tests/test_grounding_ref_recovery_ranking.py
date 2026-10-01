"""Focused recovery ranking regressions for the local #1633 patch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit

TOOL = "report_tool"
CALL_A = "call_A|fc_a"
CALL_B = "call_B|fc_b"


def _ledger(tmp_path: Path, *calls: tuple[str, dict]) -> GroundingLedger:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Summarize portfolio performance")
    for call_id, payload in calls:
        ledger.ingest_tool_result(
            tool_name=TOOL,
            arguments={},
            result=json.dumps(payload),
            call_id=call_id,
            success=True,
        )
    return ledger


def _answer(value: str, ref: str, prose: str | None = None) -> str:
    body = prose or f"Portfolio return was {value}."
    return f"{body}\n\n```figures\n{value} | observed | portfolio return | {ref}\n```"


def _returns(value: float) -> dict:
    return {"ok": True, "data": {"portfolio_return_pct": value}}


def test_tool_name_candidates_rank_matching_period_first(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        (CALL_A, _returns(-2.73)),
        (CALL_B, _returns(9.36)),
    )

    result = ledger.validate_final_answer(
        _answer("9.36%", f"{TOOL}::portfolio_return_pct")
    )

    assert result.valid is False
    issue = result.issues[0]
    assert issue["reason"] == "field_ref_needs_call_id"
    assert issue["field_ref_candidates"][0] == f"{CALL_B}::data.portfolio_return_pct"


def test_wrong_call_hints_same_field_from_call_holding_value(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        (CALL_A, _returns(-2.73)),
        (CALL_B, _returns(9.36)),
    )

    wrong = ledger.validate_final_answer(
        _answer("9.36%", f"{CALL_A}::data.portfolio_return_pct")
    )

    assert wrong.valid is False
    issue = wrong.issues[0]
    assert issue["reason"] == "not_in_referenced_call"
    assert issue["field_ref_candidates"][0] == f"{CALL_B}::data.portfolio_return_pct"

    corrected = ledger.validate_final_answer(
        _answer("9.36%", f"{CALL_B}::data.portfolio_return_pct")
    )
    assert corrected.valid is True, corrected.issues


def test_wrong_call_hint_does_not_authorize_original_ref(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        (CALL_A, _returns(-2.73)),
        (CALL_B, _returns(9.36)),
    )

    wrong = ledger.validate_final_answer(
        _answer("9.36%", f"{CALL_A}::data.portfolio_return_pct")
    )
    assert wrong.valid is False


def test_indexed_dotted_and_bracket_refs_are_equivalent_but_isolated(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "data": {
            "positions": [
                {"symbol": "AAA.US", "contribution_pct": 1.23},
                {"symbol": "BBB.US", "contribution_pct": -0.87},
            ]
        },
    }
    ledger = _ledger(tmp_path, (CALL_A, payload))

    bracket = ledger.validate_final_answer(
        _answer(
            "1.23%",
            f"{CALL_A}::data.positions[0].contribution_pct",
            "AAA.US contributed 1.23%.",
        )
    )
    dotted = ledger.validate_final_answer(
        _answer(
            "1.23%",
            f"{CALL_A}::data.positions.0.contribution_pct",
            "AAA.US contributed 1.23%.",
        )
    )
    cross = ledger.validate_final_answer(
        _answer(
            "-0.87%",
            f"{CALL_A}::data.positions[0].contribution_pct",
            "AAA.US contributed -0.87%.",
        )
    )

    assert bracket.valid is True, bracket.issues
    assert dotted.valid is True, dotted.issues
    assert cross.valid is False


def test_container_ref_stays_invalid_but_hints_exact_matching_leaf(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "data": {
            "positions": [
                {"symbol": "AAA.US", "contribution_pct": 1.23},
                {"symbol": "BBB.US", "contribution_pct": -0.87},
            ]
        },
    }
    ledger = _ledger(tmp_path, (CALL_A, payload))

    result = ledger.validate_final_answer(
        _answer(
            "1.23%",
            f"{CALL_A}::data.positions",
            "AAA.US contributed 1.23%.",
        )
    )

    assert result.valid is False
    candidates = result.issues[0]["field_ref_candidates"]
    assert candidates[0] == f"{CALL_A}::data.positions[0].contribution_pct"
    assert f"{CALL_A}::data.positions[1].contribution_pct" not in candidates


def test_feedback_deduplicates_candidate_already_rendered_as_source(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        (CALL_A, _returns(-2.73)),
        (CALL_B, _returns(9.36)),
    )
    result = ledger.validate_final_answer(
        _answer("9.36%", f"{TOOL}::portfolio_return_pct")
    )
    prompt = ledger.correction_prompt(result)
    candidate = f"{CALL_B}::data.portfolio_return_pct"
    assert prompt.count(candidate) == 1
