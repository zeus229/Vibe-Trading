"""Symbol-keyed analysis metric paths are classified without guessing generic maps."""

from __future__ import annotations

import json
import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.evidence import _metric_kind_for_path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("risk_contribution_pct.AAPL.US", "contribution"),
        ("risk_contribution_pct.YPFD", "contribution"),
        ("contribution.MSFT.US", "contribution"),
        ("correlation_matrix.AAPL.US.MSFT.US", "correlation"),
        ("correlation_matrix.YPFD.PAMP", "correlation"),
        ("other_data.AAPL.US", None),
        ("unrelated_map.YPFD", None),
        ("risk_contribution_pct.unknown", None),
    ],
)
def test_symbol_keyed_metric_paths(path: str, expected: str | None) -> None:
    assert _metric_kind_for_path(path) == expected


def _ledger(tmp_path, payload):
    ledger = GroundingLedger(
        run_dir=tmp_path,
        user_message="Compare contribution for YPFD and AAPL.US",
    )
    ledger.ingest_tool_result(
        tool_name="factor_analysis",
        arguments={},
        result=json.dumps({"status": "ok", **payload}),
        call_id="metrics-call",
        success=True,
    )
    return ledger


@pytest.mark.parametrize(("key", "symbol"), [("YPFD", "YPFD"), ("AAPL.US", "AAPL.US")])
def test_contribution_map_keeps_exact_instrument_identity(tmp_path, key, symbol):
    ledger = _ledger(tmp_path, {"risk_contribution_pct": {key: 0.125}})
    (entry,) = ledger._analysis_metrics
    assert entry["symbol"] == symbol
    assert entry["instrument_scoped"] is True
    assert ledger._metric_pool(symbol) == [0.125]
    assert ledger._metric_pool("MSFT.US") == []


def test_correlation_matrix_cells_have_two_axes_and_no_single_symbol(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"correlation_matrix": {"AAPL.US": {"MSFT.US": 0.42}}},
    )
    (entry,) = ledger._analysis_metrics
    assert entry["metric"] == "correlation"
    assert entry["symbol"] is None
    assert entry["instrument_scoped"] is True
    assert ledger._metric_pool("AAPL.US") == []
    assert ledger._metric_pool("MSFT.US") == []
    pair_claim = ledger.validate_final_answer(
        "AAPL.US y MSFT.US tienen correlación 0.42.\n\n```figures\n"
        "0.42 | observed | correlación | "
        "metrics-call::correlation_matrix.AAPL.US.MSFT.US\n```"
    )
    assert pair_claim.valid, pair_claim.issues
    single_instrument_claim = ledger.validate_final_answer(
        "AAPL.US tiene correlación 0.42.\n\n```figures\n"
        "0.42 | observed | correlación | "
        "metrics-call::correlation_matrix.AAPL.US.MSFT.US\n```"
    )
    assert not single_instrument_claim.valid


def test_equal_values_cannot_cross_instrument_reference(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"contribution_pct": {"YPFD": 0.125, "AAPL.US": 0.125}},
    )
    assert ledger._metric_pool("YPFD") == [0.125]
    assert ledger._metric_pool("AAPL.US") == [0.125]
    assert ledger._referenced(
        "metrics-call::contribution_pct.YPFD", "AAPL.US", None
    ) == ([], [])
    wrong_instrument_ref = ledger.validate_final_answer(
        "AAPL.US aporta 0.125%.\n\n```figures\n"
        "0.125% | observed | contribución | "
        "metrics-call::contribution_pct.YPFD\n```"
    )
    assert not wrong_instrument_ref.valid


@pytest.mark.parametrize(
    "key", ["TOTAL", "UNKNOWN", "AAPL.US.extra", "AAPL.US/MSFT.US"]
)
def test_ambiguous_or_aggregate_keys_do_not_get_identity(tmp_path, key):
    ledger = _ledger(tmp_path, {"risk_contribution_pct": {key: 0.125}})
    assert all(entry["symbol"] is None for entry in ledger._analysis_metrics)
    assert all(entry["instrument_scoped"] for entry in ledger._analysis_metrics)
    assert ledger._metric_pool("AAPL.US") == []


def test_valid_final_answer_passes_and_wrong_symbol_value_fails(tmp_path):
    ledger = _ledger(
        tmp_path,
        {"risk_contribution_pct": {"YPFD": 0.125, "AAPL.US": 0.25}},
    )
    valid = ledger.validate_final_answer(
        "YPFD aporta 0.125%.\n\n```figures\n"
        "0.125% | observed | contribución | "
        "metrics-call::risk_contribution_pct.YPFD\n```"
    )
    false = ledger.validate_final_answer(
        "AAPL.US aporta 0.125%.\n\n```figures\n"
        "0.125% | observed | contribución | "
        "metrics-call::risk_contribution_pct.YPFD\n```"
    )
    assert valid.valid, valid.issues
    assert not false.valid
