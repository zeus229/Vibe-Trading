"""Fork contract: a declared ISO currency denominates the money leaves below its object.

Upstream's ``test_currency_does_not_cross_into_nested_objects`` expects nested leaves to
stay unstamped; the fork keeps inheritance because real portfolio payloads declare the
currency once (``{"currency": "ARS", "totals": {...}, "positions": [...]}``). These cases
pin the ARS/USD protections that make inheritance safe: an inner declaration wins, a
sibling object never borrows it, and a claim in the other currency stays rejected.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit


def _ledger(tmp_path: Path, payload: dict) -> GroundingLedger:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Report the figures.")
    ledger.ingest_tool_result(
        tool_name="account_summary", arguments={}, result=json.dumps(payload),
        call_id="c1", success=True,
    )
    return ledger


def _currencies(ledger: GroundingLedger) -> dict[str, str | None]:
    return {record.field: record.currency for record in ledger._evidence}


def test_nested_money_leaf_inherits_the_declared_currency(tmp_path):
    got = _currencies(_ledger(tmp_path, {"data": {"currency": "ARS", "totals": {"value_end": 10.0}}}))
    assert got == {"data.totals.value_end": "ARS"}


def test_inner_declaration_overrides_the_outer_one(tmp_path):
    payload = {"data": {"currency": "ARS", "value_end": 1.0, "usd": {"currency": "USD", "value_end": 5.0}}}
    got = _currencies(_ledger(tmp_path, payload))
    assert got == {"data.value_end": "ARS", "data.usd.value_end": "USD"}


def test_sibling_object_does_not_borrow_the_currency(tmp_path):
    payload = {"data": {"a": {"currency": "USD", "value_end": 1.0}, "b": {"value_end": 2.0}}}
    got = _currencies(_ledger(tmp_path, payload))
    assert got["data.a.value_end"] == "USD"
    assert got["data.b.value_end"] is None


def test_count_leaf_is_not_stamped(tmp_path):
    got = _currencies(_ledger(tmp_path, {"data": {"currency": "ARS", "positions": 12}}))
    assert got == {"data.positions": None}


def test_claim_in_the_other_currency_is_rejected(tmp_path):
    payload = {"data": {"currency": "ARS", "totals": {"value_end": 5000.0}}}
    ledger = _ledger(tmp_path, payload)
    answer = "Ending value is USD 5000.\n\n```figures\nUSD 5000 | observed | c1::data.totals.value_end\n```"
    assert ledger.validate_final_answer(answer).valid is False
