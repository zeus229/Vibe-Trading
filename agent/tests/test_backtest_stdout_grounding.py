"""Backtest stdout and envelope bookkeeping must never mint grounding evidence."""

from __future__ import annotations

import json

from src.agent.grounding import GroundingLedger
from src.tools.backtest_tool import BacktestTool


def _ingest(ledger: GroundingLedger, stdout: str, *, status: str = "ok", exit_code: int = 0) -> None:
    ledger.ingest_tool_result(
        tool_name="backtest",
        arguments={"run_dir": str(ledger.run_dir)},
        result=json.dumps(
            {
                "status": status,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": "",
                "artifacts": {},
                "elapsed_seconds": 12.5,
                "run_dir": str(ledger.run_dir),
            }
        ),
        call_id="bt-metrics",
        success=status == "ok",
    )


def test_envelope_bookkeeping_is_not_evidence(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza GGAL")
    _ingest(ledger, "done")

    assert [r for r in ledger._evidence if r.tool == "backtest"] == []


def test_labelled_json_stdout_from_model_code_is_not_evidence(tmp_path):
    # Fork policy: numbers printed by model-authored code are unverifiable, so a
    # labelled JSON object in stdout cannot ground an observed figure.
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza GGAL")
    _ingest(ledger, 'TECHNICAL_METRICS={"GGAL.BA":{"sma21":6933.5288,"vol20_ann":0.302052}}')

    result = ledger.validate_final_answer(
        "SMA 21: 6933.5288.\n\n```figures\n6933.5288 | observed | sma21 | backtest\n```"
    )

    assert result.valid is False
    assert [r for r in ledger._evidence if r.tool == "backtest"] == []


def test_failed_backtest_stdout_never_becomes_evidence(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza GGAL")
    _ingest(ledger, 'TECHNICAL_METRICS={"GGAL.BA":{"vol20_ann":0.302052}}', status="error", exit_code=1)

    result = ledger.validate_final_answer(
        "Volatilidad 20: 30.2052%.\n\n```figures\n30.2052% | observed | vol20_ann | backtest\n```"
    )

    assert result.valid is False


def test_backtest_result_is_preserved_during_microcompact():
    assert BacktestTool.preserve_during_microcompact is True
