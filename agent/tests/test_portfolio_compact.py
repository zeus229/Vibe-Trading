"""Snapshot identity, accounting, visibility and grounding for compact portfolios."""

from copy import deepcopy
import json
from decimal import Decimal

import pytest

from src.portfolio.compact import CompactPortfolioError, compact_snapshot
from src.portfolio.service import PortfolioService
from src.tools.portfolio_tool import PortfolioSummaryTool
from src.config.limits import truncate_tool_result
from src.agent.grounding import GroundingLedger


def snapshot():
    rows = [
        {
            "symbol": s,
            "source_instrument_type": "ACCIONES",
            "country": "Argentina",
            "native_currency": "ARS",
            "market_value_native": float(i + 1),
            "priced": True,
        }
        for i, s in enumerate(["GGAL"] + [f"SYM{i}" for i in range(35)] + ["BMA", "BBAR"])
    ]
    invested = sum(r["market_value_native"] for r in rows)
    return {
        "snapshot_id": "persisted-id",
        "created_at": "2026-10-07T03:19:00+00:00",
        "complete": True,
        "valuation_version": 3,
        "totals": {"usd": 0.0, "cny": 0.0, "native_by_currency": {"ARS": invested + 32}},
        "accounts": [
            {
                "status": "ok",
                "native_currency": "ARS",
                "total_native": invested + 32,
                "cash_native": 10.0,
                "unsettled_cash_native": 20.0,
                "unpriced_or_other_native": 2.0,
            }
        ],
        "positions": rows,
    }


def test_single_snapshot_read_and_no_mutation(monkeypatch):
    source = snapshot()
    original = deepcopy(source)
    reads = []

    def latest(self):
        reads.append(1)
        assert len(reads) == 1
        return source

    monkeypatch.setattr(PortfolioService, "latest", latest)
    service = object.__new__(PortfolioService)
    result = service.compact_analysis_context()
    assert result["snapshot_id"] == source["snapshot_id"]
    assert result["as_of"] == source["created_at"]
    assert source == original


def test_complete_math_cash_and_country():
    source = snapshot()
    c = compact_snapshot(source)
    total = c["totals"]["portfolio_market_value_ars"]
    invested = sum(x["market_value_ars"] for x in c["holdings"])
    assert invested + 10 + 20 + 2 == total
    assert c["totals"]["cash_ars"] == 10
    assert c["totals"]["unsettled_cash_ars"] == 20
    assert c["aggregates"]["by_country"]["Argentina"]["market_value_ars"] == invested
    assert c["aggregates"]["by_instrument_type"]["ACCIONES"]["market_value_ars"] == invested
    assert c["aggregates"]["by_instrument_type"]["EFECTIVO"]["market_value_ars"] == 30
    assert len(c["holdings"]) == 38
    for row in c["holdings"]:
        assert row["weight_portfolio"] == float(
            (Decimal(str(row["market_value_ars"])) / Decimal(str(total))).quantize(Decimal("0.00000001"))
        )


def test_tool_budget_and_simultaneous_visibility(monkeypatch):
    monkeypatch.setattr(
        "src.tools.portfolio_tool.PortfolioService",
        lambda: type("Service", (), {"compact_analysis_context": lambda self: compact_snapshot(snapshot())})(),
    )
    raw = PortfolioSummaryTool().execute(view="compact")
    assert len(raw) < 9000
    assert truncate_tool_result(raw) == raw
    c = json.loads(raw)["context"]
    assert c["totals"]["portfolio_market_value_ars"] > 0
    assert "Argentina" in c["aggregates"]["by_country"]
    assert {"GGAL", "BMA", "BBAR"} <= {x["symbol"] for x in c["holdings"]}


def test_grounding_entities_units_and_aggregate(tmp_path):
    c = compact_snapshot(snapshot())
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Composición de GGAL BMA BBAR")
    ledger.ingest_tool_result(
        tool_name="portfolio_summary",
        arguments={"view": "compact"},
        result=json.dumps({"status": "ok", "context": c}),
        call_id="compact-call",
        success=True,
    )
    evidence = {e.field: e for e in ledger._evidence}
    for i in (0, 36, 37):
        e = evidence[f"context.holdings[{i}].market_value_ars"]
        assert (e.symbol, e.identity_scope, e.unit, e.currency) == (
            c["holdings"][i]["symbol"],
            "entity",
            "money",
            "ARS",
        )
        assert evidence[f"context.holdings[{i}].weight_portfolio"].unit == "ratio"
    for path in (
        "totals.portfolio_market_value_ars",
        "totals.cash_ars",
        "totals.unsettled_cash_ars",
        "aggregates.by_country.Argentina.market_value_ars",
    ):
        e = evidence["context." + path]
        assert (e.symbol, e.identity_scope, e.unit, e.currency) == (None, "aggregate", "money", "ARS")
        assert e.timestamp == c["as_of"]


@pytest.mark.parametrize("change", ["currency", "unpriced", "cash_missing", "mismatch", "identity"])
def test_unsupported_snapshots_fail_closed(change):
    s = snapshot()
    if change == "currency":
        s["positions"][0]["native_currency"] = "USD"
    if change == "unpriced":
        s["positions"][0]["priced"] = False
    if change == "cash_missing":
        del s["accounts"][0]["cash_native"]
    if change == "mismatch":
        s["totals"]["native_by_currency"]["ARS"] += 1
    if change == "identity":
        del s["snapshot_id"]
    with pytest.raises(CompactPortfolioError):
        compact_snapshot(s)


def test_budget_overflow_returns_error_without_partial_holdings(monkeypatch):
    s = snapshot()
    s["positions"][0]["symbol"] = "X" * 10000
    monkeypatch.setattr(
        "src.tools.portfolio_tool.PortfolioService",
        lambda: type("Service", (), {"compact_analysis_context": lambda self: compact_snapshot(s)})(),
    )
    result = json.loads(PortfolioSummaryTool().execute(view="compact"))
    assert result["status"] == "error"
    assert "context" not in result


def test_multiple_countries_types_and_settlement_accounting():
    s = snapshot()
    for i, row in enumerate(s["positions"]):
        row["source_instrument_type"] = ("ACCIONES", "CEDEARS", "BONOS", "FCI")[i % 4]
        row["country"] = "Argentina" if i % 2 else "Estados Unidos"
    c = compact_snapshot(s)
    for country in ("Argentina", "Estados Unidos"):
        expected = sum(r["market_value_native"] for r in s["positions"] if r["country"] == country)
        bucket = c["aggregates"]["by_country"][country]
        assert bucket["market_value_ars"] == expected
        assert bucket["weight"] == expected / c["totals"]["portfolio_market_value_ars"]
    for category in ("ACCIONES", "CEDEARS", "BONOS", "FCI"):
        assert c["aggregates"]["by_instrument_type"][category]["market_value_ars"] == sum(
            r["market_value_native"] for r in s["positions"] if r["source_instrument_type"] == category
        )
    assert (
        sum(b["market_value_ars"] for b in c["aggregates"]["by_instrument_type"].values()) + 2
        == c["totals"]["portfolio_market_value_ars"]
    )


def test_default_and_explicit_extended_preserve_legacy_payload(monkeypatch):
    from src.tools.portfolio_tool import _reorder_for_truncation_safety

    context = {
        "as_of": "2026-10-06",
        "totals": {"native_by_currency": {"ARS": 1}},
        "holdings_native": {"ARS": []},
        "privacy": "legacy",
    }
    monkeypatch.setattr(
        "src.tools.portfolio_tool.PortfolioService",
        lambda: type("Service", (), {"analysis_context": lambda self: deepcopy(context)})(),
    )
    expected = json.dumps({"status": "ok", "context": _reorder_for_truncation_safety(context)}, ensure_ascii=False)
    assert PortfolioSummaryTool().execute() == expected
    assert PortfolioSummaryTool().execute(view="extended") == expected


def test_empty_and_invalid_view(monkeypatch):
    monkeypatch.setattr(
        "src.tools.portfolio_tool.PortfolioService",
        lambda: type("Service", (), {"compact_analysis_context": lambda self: None})(),
    )
    assert json.loads(PortfolioSummaryTool().execute(view="compact"))["status"] == "empty"
    assert json.loads(PortfolioSummaryTool().execute(view="unknown"))["status"] == "error"


def test_compact_is_semantic_subset_of_extended_from_one_snapshot(monkeypatch):
    source = snapshot()
    source["warnings"] = []
    source["accounts"][0]["broker"] = "asistente-casa"
    source["positions"][1]["country"] = None
    service = object.__new__(PortfolioService)
    with_source = deepcopy(source)
    reads = []

    def latest(self):
        reads.append(1)
        return deepcopy(with_source)

    monkeypatch.setattr(PortfolioService, "latest", latest)
    monkeypatch.setattr("src.tools.portfolio_tool.PortfolioService", lambda: service)
    extended = json.loads(PortfolioSummaryTool().execute())["context"]
    compact = json.loads(PortfolioSummaryTool().execute(view="compact"))["context"]
    explicit = json.loads(PortfolioSummaryTool().execute(view="extended"))["context"]
    assert explicit == extended
    assert len(reads) == 3
    for key in ("snapshot_id", "as_of", "valuation_version"):
        assert compact[key] == extended[key]
    assert compact["totals"]["portfolio_market_value_ars"] == extended["totals"]["native_by_currency"]["ARS"]
    rows = extended["holdings_native"]["ARS"]
    assert len(rows) == len(compact["holdings"]) == 38
    expected = [
        {
            "symbol": r["symbol"],
            "instrument_type": r["source_instrument_type"],
            "country": r["country"],
            "market_value_ars": r["market_value_native"],
            "native_currency": r["native_currency"],
            "weight_portfolio": r["weight"],
        }
        for r in rows
    ]
    assert compact["holdings"] == expected
    for compact_key, extended_key in (
        ("cash_ars", "cash_native"),
        ("unsettled_cash_ars", "unsettled_cash_native"),
        ("unpriced_or_other_ars", "unpriced_or_other_native"),
    ):
        assert compact["totals"][compact_key] == sum(r[extended_key] for r in extended["account_allocation"])
    assert source == with_source


def test_compact_view_can_be_pinned_to_a_snapshot_id(monkeypatch):
    """A pinned compact read goes to the snapshot id, a missing id fails closed."""
    import json

    from src.tools.portfolio_tool import PortfolioSummaryTool

    seen = {}

    def pinned(self, snapshot_id=None):
        seen["id"] = snapshot_id
        return None

    monkeypatch.setattr(
        "src.portfolio.service.PortfolioService.compact_analysis_context", pinned
    )
    payload = json.loads(PortfolioSummaryTool().execute(view="compact", snapshot_id="abc"))

    assert seen["id"] == "abc"
    assert payload["status"] == "error"
    assert payload["error_code"] == "snapshot_not_found"
