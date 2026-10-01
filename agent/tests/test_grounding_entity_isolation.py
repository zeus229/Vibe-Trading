"""Entity isolation for structured grounding evidence (synthetic, offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit

KEYED = {"data": {"AAA.US": {"value": 101.25}, "BBB.US": {"value": 202.5}}}
LISTED = {"data": {"items": [{"symbol": "AAA.US", "value": 101.25}, {"symbol": "BBB.US", "value": 202.5}]}}
ANONYMOUS = {"data": {"items": [{"value": 101.25}, {"value": 202.5}]}}
MARKET = {"AAA.US": [{"close": 101.25}], "BBB.US": [{"close": 202.5}]}


def _ledger(
    tmp_path: Path,
    payload: dict,
    *,
    arguments: dict | None = None,
    tool: str = "synthetic_result",
    message: str = "Compare AAA.US and BBB.US observations",
) -> GroundingLedger:
    ledger = GroundingLedger(run_dir=tmp_path, user_message=message)
    ledger.ingest_tool_result(
        tool_name=tool,
        arguments=arguments or {},
        result=json.dumps(payload),
        call_id="call_1",
        success=True,
    )
    return ledger


def _claim(ledger: GroundingLedger, subject: str, value: float, ref: str, *, valid: bool) -> None:
    body = f"{subject} observation {value}."
    answer = f"{body}\n\n```figures\n{value} | observed | observation | {ref}\n```"
    result = ledger.validate_final_answer(answer)
    assert result.valid is valid, result.issues
    if valid:
        assert result.released_text == body


@pytest.mark.parametrize(
    ("name", "payload", "arguments", "subject", "value", "ref", "valid"),
    [
        ("key_correct_exact", KEYED, {}, "AAA.US", 101.25, "call_1::data.AAA.US.value", True),
        ("key_cross_exact", KEYED, {}, "AAA.US", 202.5, "call_1::data.BBB.US.value", False),
        ("key_cross_single_arg", KEYED, {"symbol": "AAA.US"}, "AAA.US", 202.5, "call_1::data.BBB.US.value", False),
        ("key_wrong_own_ref", KEYED, {}, "AAA.US", 202.5, "call_1::data.AAA.US.value", False),
        ("key_cross_call_scope", KEYED, {}, "AAA.US", 202.5, "call_1", False),
        ("key_ambiguous_short_ref", KEYED, {}, "AAA.US", 202.5, "value", False),
        ("key_wrong_value", KEYED, {}, "AAA.US", 999.5, "call_1::data.AAA.US.value", False),
        ("list_correct_exact", LISTED, {}, "AAA.US", 101.25, "call_1::data.items[0].value", True),
        ("list_cross_exact", LISTED, {}, "AAA.US", 202.5, "call_1::data.items[1].value", False),
        ("list_cross_single_arg", LISTED, {"symbol": "AAA.US"}, "AAA.US", 202.5, "call_1::data.items[1].value", False),
        ("list_wrong_own_ref", LISTED, {}, "AAA.US", 202.5, "call_1::data.items[0].value", False),
        ("list_cross_call_scope", LISTED, {}, "AAA.US", 202.5, "call_1", False),
        ("list_ambiguous_short_ref", LISTED, {}, "AAA.US", 202.5, "value", False),
        ("anonymous_cross_exact", ANONYMOUS, {}, "AAA.US", 202.5, "call_1::data.items[1].value", False),
        (
            "anonymous_multi_args",
            ANONYMOUS,
            {"symbols": ["AAA.US", "BBB.US"]},
            "AAA.US",
            202.5,
            "call_1::data.items[1].value",
            False,
        ),
        (
            "equal_values_wrong_element",
            {"data": {"AAA.US": {"value": 101.25}, "BBB.US": {"value": 101.25}}},
            {},
            "AAA.US",
            101.25,
            "call_1::data.BBB.US.value",
            False,
        ),
        (
            "single_arg_anonymous",
            {"data": {"value": 101.25}},
            {"symbol": "AAA.US"},
            "AAA.US",
            101.25,
            "call_1::data.value",
            True,
        ),
    ],
)
def test_generic_entity_isolation(
    name: str, payload: dict, arguments: dict, subject: str, value: float, ref: str, valid: bool, tmp_path: Path
) -> None:
    ledger = _ledger(tmp_path, payload, arguments=arguments)
    _claim(ledger, subject, value, ref, valid=valid)


@pytest.mark.parametrize(
    "payload, expected",
    [
        (KEYED, {"data.AAA.US.value": "AAA.US", "data.BBB.US.value": "BBB.US"}),
        (LISTED, {"data.items[0].value": "AAA.US", "data.items[1].value": "BBB.US"}),
    ],
)
def test_ledger_preserves_per_item_identity(tmp_path: Path, payload: dict, expected: dict[str, str]) -> None:
    ledger = _ledger(tmp_path, payload)
    actual = {record.field: record.symbol for record in ledger._evidence if record.value is not None}
    assert actual == expected
    artifact = json.loads((tmp_path / "artifacts" / "grounding_evidence.json").read_text())
    assert {item["field"]: item["symbol"] for item in artifact["evidence"] if item["value"] is not None} == expected


def test_conflicting_argument_does_not_relabel_item(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, KEYED, arguments={"symbol": "AAA.US"})
    assert next(record for record in ledger._evidence if record.field == "data.BBB.US.value").symbol == "BBB.US"
    _claim(ledger, "AAA.US", 202.5, "call_1::data.BBB.US.value", valid=False)
    _claim(ledger, "BBB.US", 202.5, "call_1::data.BBB.US.value", valid=False)


def test_anonymous_multi_entity_evidence_remains_unattributed(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, ANONYMOUS, arguments={"symbols": ["AAA.US", "BBB.US"]})
    assert all(record.symbol is None for record in ledger._evidence)
    _claim(ledger, "AAA.US", 202.5, "call_1::data.items[1].value", valid=False)


@pytest.mark.parametrize(
    "subject,value,valid", [("AAA.US synthetic USD price", 101.25, True), ("AAA.US synthetic USD price", 202.5, False)]
)
def test_get_market_data_still_isolates_symbols(tmp_path: Path, subject: str, value: float, valid: bool) -> None:
    ledger = _ledger(
        tmp_path, MARKET, arguments={"codes": ["AAA.US", "BBB.US"], "source": "synthetic"}, tool="get_market_data"
    )
    assert {(record.symbol, record.value) for record in ledger._evidence} == {("AAA.US", 101.25), ("BBB.US", 202.5)}
    _claim(ledger, subject, value, "call_1", valid=valid)


@pytest.mark.parametrize(
    "ref,value,valid",
    [("call_1::data.AAA.US.sharpe", 1.25, True), ("call_1::data.BBB.US.sharpe", 2.5, False), ("sharpe", 2.5, False)],
)
def test_analysis_metric_isolation(tmp_path: Path, ref: str, value: float, valid: bool) -> None:
    payload = {"status": "ok", "data": {"AAA.US": {"sharpe": 1.25}, "BBB.US": {"sharpe": 2.5}}}
    ledger = _ledger(tmp_path, payload, tool="factor_analysis")
    _claim(ledger, "AAA.US Sharpe", value, ref, valid=valid)
    assert {entry["field"]: entry.get("symbol") for entry in ledger._analysis_metrics} == {
        "data.AAA.US.sharpe": "AAA.US",
        "data.BBB.US.sharpe": "BBB.US",
    }


def test_aggregate_metric_remains_available_only_to_unscoped_claim(tmp_path: Path) -> None:
    ledger = _ledger(
        tmp_path,
        {"status": "ok", "aggregate": {"sharpe": 1.75}},
        tool="factor_analysis",
        message="Summarize aggregate analysis",
    )
    _claim(ledger, "Overall Sharpe", 1.75, "call_1::aggregate.sharpe", valid=True)
    scoped = _ledger(tmp_path / "scoped", {"status": "ok", "aggregate": {"sharpe": 1.75}}, tool="factor_analysis")
    _claim(scoped, "AAA.US Sharpe", 1.75, "call_1::aggregate.sharpe", valid=False)


def test_indexed_ref_boundaries_remain_strict(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, LISTED)
    _claim(ledger, "AAA.US", 202.5, "call_1::data.items[0].value", valid=False)
    _claim(ledger, "AAA.US", 101.25, "call_1::data.items", valid=False)


@pytest.mark.parametrize(
    "payload, ref", [(KEYED, "call_1::data.AAA.US.value"), (LISTED, "call_1::data.items[0].value")]
)
def test_multiple_arguments_keep_each_explicit_entity(tmp_path: Path, payload: dict, ref: str) -> None:
    ledger = _ledger(tmp_path, payload, arguments={"symbols": ["AAA.US", "BBB.US"]})
    assert {record.symbol for record in ledger._evidence} == {"AAA.US", "BBB.US"}
    _claim(ledger, "AAA.US", 101.25, ref, valid=True)
    _claim(ledger, "AAA.US", 202.5, "call_1", valid=False)


def test_anonymous_list_with_one_global_argument_is_not_assumed_single_entity(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, ANONYMOUS, arguments={"symbol": "AAA.US"})
    assert all(record.symbol is None for record in ledger._evidence)
    _claim(ledger, "AAA.US", 202.5, "call_1::data.items[1].value", valid=False)


def test_opaque_explicit_entity_ids_are_isolated_even_with_equal_values(tmp_path: Path) -> None:
    payload = {"data": {"items": [{"entity_id": "first-id", "value": 1.25}, {"entity_id": "second-id", "value": 1.25}]}}
    ledger = _ledger(tmp_path, payload, message="Compare first-id and second-id")
    assert [record.symbol for record in ledger._evidence] == ["FIRST-ID", "SECOND-ID"]
    _claim(ledger, "first-id", 1.25, "call_1::data.items[0].value", valid=True)
    _claim(ledger, "first-id", 1.25, "call_1::data.items[1].value", valid=False)


def test_explicit_aggregate_inside_multi_entity_result_stays_unscoped(tmp_path: Path) -> None:
    payload = {
        "status": "ok",
        "data": {"AAA.US": {"sharpe": 1.25}, "BBB.US": {"sharpe": 2.5}},
        "aggregate": {"sharpe": 1.75},
    }
    ledger = _ledger(tmp_path, payload, tool="factor_analysis")
    aggregate = next(entry for entry in ledger._analysis_metrics if entry["field"] == "aggregate.sharpe")
    assert aggregate["symbol"] is None and aggregate["identity_scope"] == "aggregate"
    _claim(ledger, "Overall Sharpe", 1.75, "call_1::aggregate.sharpe", valid=True)
    _claim(ledger, "AAA.US Sharpe", 1.75, "call_1::aggregate.sharpe", valid=False)


def test_equal_values_different_list_elements_remain_isolated(tmp_path: Path) -> None:
    payload = {"data": {"items": [{"symbol": "AAA.US", "value": 1.25}, {"symbol": "BBB.US", "value": 1.25}]}}
    ledger = _ledger(tmp_path, payload)
    _claim(ledger, "AAA.US", 1.25, "call_1::data.items[0].value", valid=True)
    _claim(ledger, "AAA.US", 1.25, "call_1::data.items[1].value", valid=False)


def test_conflicting_explicit_fields_fail_closed(tmp_path: Path) -> None:
    payload = {"data": {"items": [{"symbol": "AAA.US", "entity_id": "BBB.US", "value": 1.25}]}}
    ledger = _ledger(tmp_path, payload)
    record = next(record for record in ledger._evidence if record.value == 1.25)
    assert record.identity_scope == "conflict"
    _claim(ledger, "AAA.US", 1.25, "call_1::data.items[0].value", valid=False)
    _claim(ledger, "BBB.US", 1.25, "call_1::data.items[0].value", valid=False)


def test_status_code_is_not_an_entity_identity(tmp_path: Path) -> None:
    payload = {"status": "ok", "code": "OK", "data": {"AAA.US": {"value": 1.25}}}
    ledger = _ledger(tmp_path, payload)
    record = next(record for record in ledger._evidence if record.value == 1.25)
    assert record.symbol == "AAA.US" and record.identity_scope == "entity"
    _claim(ledger, "AAA.US", 1.25, "call_1::data.AAA.US.value", valid=True)


def test_canonical_code_is_an_explicit_item_identity(tmp_path: Path) -> None:
    payload = {"data": {"items": [{"code": "AAA.US", "value": 1.25}, {"code": "BBB.US", "value": 2.5}]}}
    ledger = _ledger(tmp_path, payload)
    assert [record.symbol for record in ledger._evidence] == ["AAA.US", "BBB.US"]
    _claim(ledger, "AAA.US", 1.25, "call_1::data.items[0].value", valid=True)
    _claim(ledger, "AAA.US", 2.5, "call_1::data.items[1].value", valid=False)
