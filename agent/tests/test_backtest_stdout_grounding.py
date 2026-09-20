"""Grounding regressions for structured metrics emitted by successful backtests."""

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
                "run_dir": str(ledger.run_dir),
            }
        ),
        call_id="bt-metrics",
        success=status == "ok",
    )


def test_successful_backtest_stdout_allowlist_grounds_ggal_metrics(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza GGAL")
    _ingest(
        ledger,
        (
            'TECHNICAL_METRICS={"GGAL.BA":{"sma21":6933.5287853422615,'
            '"sma42":7158.9736,"avg_volume_20":1612178.95,'
            '"vol20_ann":0.302052,"vol60_ann":0.366920,'
            '"support20":6552.2,"support60":6442.4,'
            '"resistance20":7311.3,"resistance60":8371.2}}'
        ),
    )

    result = ledger.validate_final_answer(
        "SMA 21: 6933.5288; SMA 42: 7158.9736; volumen promedio 20: 1612178.95; "
        "soporte: 6552.2; resistencia: 7311.3; volatilidad 20: 30.2052%; "
        "volatilidad 60: 36.6920%.\n\n"
        "```figures\n"
        "6933.5288 | observed | sma21 | backtest\n"
        "7158.9736 | observed | sma42 | backtest\n"
        "1612178.95 | observed | avg_volume20 | backtest\n"
        "6552.2 | observed | support20 | backtest\n"
        "7311.3 | observed | resistance20 | backtest\n"
        "30.2052% | derived | 0.302052 * 100 | backtest\n"
        "36.6920% | derived | 0.366920 * 100 | backtest\n"
        "```"
    )

    assert result.valid is True, result.issues


def test_backtest_stdout_does_not_ingest_arbitrary_key_value_text(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza GGAL")
    _ingest(
        ledger,
        'GGAL_TECHNICAL_RESULT={"GGAL.BA":{"invented_target":999999,"foo":42,'
        '"debug_value":123.45}}',
    )

    result = ledger.validate_final_answer(
        "El valor calculado es 999999.\n\n"
        "```figures\n999999 | observed | invented_target | backtest\n```"
    )

    assert result.valid is False
    assert any(
        issue.get("code") in {"numeric_claim_conflict", "numeric_claim_unavailable"}
        for issue in result.issues
    )


def test_failed_backtest_stdout_never_becomes_evidence(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza GGAL")
    _ingest(
        ledger,
        'TECHNICAL_METRICS={"GGAL.BA":{"vol20_ann":0.302052}}',
        status="error",
        exit_code=1,
    )

    result = ledger.validate_final_answer(
        "Volatilidad 20: 30.2052%.\n\n"
        "```figures\n30.2052% | observed | vol20_ann | backtest\n```"
    )

    assert result.valid is False



def test_backtest_stdout_key_value_text_is_not_evidence(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza GGAL")
    _ingest(ledger, "sma21=6933.5288 vol20_ann=0.302052")

    result = ledger.validate_final_answer(
        "SMA 21: 6933.5288.\n\n"
        "```figures\n6933.5288 | observed | sma21 | backtest\n```"
    )

    assert result.valid is False


def test_backtest_stdout_real_aliases_are_normalized(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza GGAL")
    _ingest(
        ledger,
        (
            'GGAL_TECHNICAL_RESULT={"GGAL.BA":{"sma_21":6933.5288,'
            '"avg_volume_20":1612178.95,"vol20_annualized":0.302052,'
            '"support_20":6507.28,"resistance_20":8316.36}}'
        ),
    )

    fields = {record.field: record.value for record in ledger._evidence if record.tool == "backtest"}
    assert fields["stdout.sma21"] == 6933.5288
    assert fields["stdout.avg_volume20"] == 1612178.95
    assert fields["stdout.vol20_ann"] == 0.302052
    assert fields["stdout.support20"] == 6507.28
    assert fields["stdout.resistance20"] == 8316.36


def test_backtest_result_is_preserved_during_microcompact():
    assert BacktestTool.preserve_during_microcompact is True
