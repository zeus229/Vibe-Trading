"""Grounding classifies a numeric leaf by its unit, not by its path or its name.

Two regressions the same defect produced:

* a ratio or percent leaf that sits under a path naming a currency
  (``holdings_native.ARS[0].weight``) was taken for money and could no longer
  ground a percent;
* a monetary leaf whose name is not a value/amount/total suffix
  (``totalDebt``, ``totalRevenue``) could not ground a currency figure although
  the payload declared the currency.

A unit comes from the evidence: an explicit per-leaf ``_units`` entry, else a
declared currency scope combined with the leaf not being a ratio or a count. An
exact field ref proves where a value came from; it never changes its unit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit


def _ledger(tmp_path: Path, tool: str, payload: dict[str, Any], args: dict[str, Any]) -> GroundingLedger:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analyze AAA.US fundamentals")
    ledger.ingest_tool_result(
        tool_name=tool,
        arguments=args,
        result=json.dumps(payload),
        call_id="c1",
        success=True,
    )
    return ledger


def _issues(ledger: GroundingLedger, prose: str, value: str, ref: str, role: str = "observed", subject: str = "AAA.US") -> list[tuple[str, str]]:
    text = f"{subject} {prose}\n\n```figures\n{value} | {role} | {subject} {prose} | {ref}\n```"
    return [(str(i.get("value")), str(i.get("reason"))) for i in ledger.revalidate(text).issues]


PROFILE = {
    "ok": True,
    "data": {
        "ticker": "AAA.US",
        "listing": {"symbol": "AAA", "currency": "USD", "financial_currency": "USD"},
        "sections": {
            "key_stats": {
                "enterpriseValue": 3_000_000_000_000,
                "enterpriseToEbitda": 23.609,
                "beta": 1.225,
                "forwardPE": 22.62,
                "sharesOutstanding": 5_867_155_790,
                "heldPercentInsiders": 0.01596,
            },
            "financials": {
                "totalRevenue": 390_000_000_000,
                "totalCash": 60_000_000_000,
                "totalDebt": 98_000_000_000,
                "revenueGrowth": 0.242,
                "grossMargins": 0.60897,
                "returnOnEquity": 0.48676,
                "numberOfAnalystOpinions": 54,
            },
        },
    },
}


@pytest.fixture
def profile(tmp_path: Path) -> GroundingLedger:
    return _ledger(tmp_path, "get_stock_profile", PROFILE, {"ticker": "AAA.US"})


# --- money: declared currency scope, any monetary leaf name -----------------


@pytest.mark.parametrize(
    ("label", "value", "field"),
    [
        ("total debt is 98,000,000,000 USD.", "98000000000", "totalDebt"),
        ("total revenue is 390,000,000,000 USD.", "390000000000", "totalRevenue"),
        ("total cash is 60,000,000,000 USD.", "60000000000", "totalCash"),
        ("enterprise value is 3,000,000,000,000 USD.", "3000000000000", "enterpriseValue"),
    ],
)
def test_declared_currency_scope_grounds_monetary_leaves(profile, label, value, field) -> None:
    assert _issues(profile, label, value, "get_stock_profile") == []
    exact = f"c1::data.sections.{'key_stats' if field == 'enterpriseValue' else 'financials'}.{field}"
    assert _issues(profile, label, value, exact) == []


def test_undeclared_unit_does_not_ground_money(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "data": {"ticker": "AAA.US", "financials": {"totalDebt": 98_000_000_000}},
    }
    ledger = _ledger(tmp_path, "get_stock_profile", payload, {"ticker": "AAA.US"})

    found = _issues(ledger, "total debt is 98,000,000,000 USD.", "98000000000", "get_stock_profile")

    assert found and found[0][1] == "not_in_referenced_call"


# --- ratios, percents and counts are never money ----------------------------


@pytest.mark.parametrize(
    ("label", "value", "field"),
    [
        ("revenue growth is 0.242 USD.", "0.242", "revenueGrowth"),
        ("gross margins are 0.60897 USD.", "0.60897", "grossMargins"),
        ("shares outstanding are 5,867,155,790 USD.", "5867155790", "sharesOutstanding"),
        ("analyst opinions are 54 USD.", "54", "numberOfAnalystOpinions"),
    ],
)
def test_ratios_and_counts_do_not_ground_a_currency_figure(profile, label, value, field) -> None:
    assert _issues(profile, label, value, "get_stock_profile")
    # An exact field ref proves provenance; it does not turn the value into money.
    section = "key_stats" if field == "sharesOutstanding" else "financials"
    assert _issues(profile, label, value, f"c1::data.sections.{section}.{field}")


@pytest.mark.parametrize(
    ("label", "value", "field"),
    [
        ("revenue growth is 24.2%.", "24.2%", "revenueGrowth"),
        ("return on equity is 48.676%.", "48.676%", "returnOnEquity"),
        ("gross margins are 60.897%.", "60.897%", "grossMargins"),
    ],
)
def test_ratio_leaves_ground_percents(profile, label, value, field) -> None:
    assert _issues(profile, label, value, "get_stock_profile") == []
    assert _issues(profile, label, value, f"c1::data.sections.financials.{field}") == []


def test_a_money_leaf_does_not_ground_a_percent(profile) -> None:
    assert _issues(profile, "total debt is 98000000000%.", "98000000000%", "get_stock_profile")


def test_exact_ref_to_another_field_is_rejected(profile) -> None:
    found = _issues(
        profile,
        "total debt is 390,000,000,000 USD.",
        "390000000000",
        "c1::data.sections.financials.totalDebt",
    )
    assert found and found[0][1] == "not_in_referenced_call"

    found = _issues(
        profile,
        "revenue growth is 48.676%.",
        "48.676%",
        "c1::data.sections.financials.revenueGrowth",
    )
    assert found and found[0][1] == "not_in_referenced_call"


# --- a currency named by the path is not a unit for every leaf below it -----


HOLDINGS = {
    "status": "ok",
    "context": {
        "as_of": "2026-09-28T23:09:10Z",
        "totals": {"native_by_currency": {"ARS": 217010480.5}},
        "holdings_native": {
            "ARS": [
                {
                    "symbol": "AAA.US",
                    "market_value_native": 44930050.0,
                    "native_currency": "ARS",
                    "weight": 0.20704092,
                    "daily_change_pct": -1.6,
                }
            ]
        },
    },
}


@pytest.fixture
def holdings(tmp_path: Path) -> GroundingLedger:
    return _ledger(tmp_path, "portfolio_summary", HOLDINGS, {})


@pytest.mark.parametrize(
    ("label", "value", "field"),
    [
        ("weight is 20.704092%.", "20.704092%", "weight"),
        ("daily change is -1.6%.", "-1.6%", "daily_change_pct"),
    ],
)
def test_ratio_leaf_under_a_currency_path_grounds_a_percent(holdings, label, value, field) -> None:
    assert _issues(holdings, label, value, "portfolio_summary") == []
    exact = f"c1::context.holdings_native.ARS[0].{field}"
    assert _issues(holdings, label, value, exact) == []


def test_ratio_leaf_under_a_currency_path_is_not_money(holdings) -> None:
    assert _issues(holdings, "weight is 0.20704092 ARS.", "0.20704092", "portfolio_summary")
    assert _issues(holdings, "daily change is -1.6 ARS.", "-1.6", "portfolio_summary")


def test_monetary_leaf_under_a_currency_path_still_grounds_money(holdings) -> None:
    assert _issues(holdings, "position value is 44,930,050 ARS.", "44930050", "portfolio_summary") == []
    assert (
        _issues(
            holdings,
            "position value is 44,930,050 ARS.",
            "44930050",
            "c1::context.holdings_native.ARS[0].market_value_native",
        )
        == []
    )
    assert _issues(holdings, "portfolio is 217,010,480.5 ARS.", "217010480.5", "portfolio_summary", subject="") == []


# --- an explicit per-leaf unit is decisive ----------------------------------


STATEMENT = {
    "ok": True,
    "data": {
        "AAA.US": {
            "periods": [
                {
                    "REPORT_DATE": "2026-06-30",
                    "_units": {
                        "Revenues": "USD",
                        "EarningsPerShareDiluted": "USD/shares",
                        "WeightedAverageShares": "shares",
                    },
                    "Revenues": 119_796_000_000.0,
                    "EarningsPerShareDiluted": 9.11,
                    "WeightedAverageShares": 12_300_000_000.0,
                }
            ]
        }
    },
}


def test_explicit_unit_declares_money_without_a_currency_scope(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "get_financial_statements", STATEMENT, {"code": "AAA.US"})

    assert _issues(ledger, "revenue is 119,796,000,000 USD.", "119796000000", "get_financial_statements") == []
    exact = "c1::data.AAA.US.periods[0].Revenues"
    assert _issues(ledger, "revenue is 119,796,000,000 USD.", "119796000000", exact) == []


def test_explicit_non_currency_unit_is_not_money(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, "get_financial_statements", STATEMENT, {"code": "AAA.US"})

    assert _issues(ledger, "diluted shares are 12,300,000,000 USD.", "12300000000", "get_financial_statements")
    assert _issues(ledger, "diluted EPS is 9.11 USD.", "9.11", "get_financial_statements")


def test_explicit_ratio_unit_is_not_money_and_grounds_a_percent(tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "data": {
            "currency": "USD",
            "metrics": {"_units": {"Leverage": "x", "Coverage": "pct"}, "Leverage": 2.5, "Coverage": 41.0},
        },
    }
    ledger = _ledger(tmp_path, "get_stock_profile", payload, {"ticker": "AAA.US"})

    assert _issues(ledger, "leverage is 2.5 USD.", "2.5", "get_stock_profile")
    assert _issues(ledger, "coverage is 41.0%.", "41.0%", "get_stock_profile") == []


def test_symbol_keyed_weights_stay_ratios_inside_a_currency_scope(tmp_path: Path) -> None:
    payload = {
        "status": "ok",
        "currency": "ARS",
        "risk": {"weights": {"AAA.US": 0.21387334, "BBB.US": 0.03799221}},
    }
    ledger = _ledger(tmp_path, "portfolio_summary", payload, {})

    assert _issues(ledger, "weight is 21.387334%.", "21.387334%", "portfolio_summary") == []
    assert _issues(ledger, "weight is 0.21387334 ARS.", "0.21387334", "portfolio_summary")
    assert {r.unit for r in ledger._evidence if r.field.startswith("risk.weights")} == {"ratio"}


def test_a_count_does_not_ground_a_percent(profile) -> None:
    assert _issues(profile, "analyst opinions are 54%.", "54%", "get_stock_profile")


def test_currency_suffix_in_a_field_name_is_a_unit_declaration(tmp_path: Path) -> None:
    payload = {
        "data": {
            "currency": "ARS",
            "positions": [{"income_capital_return_ars": 6178.83, "portfolio_return_pct": -5.83}],
        }
    }
    ledger = _ledger(tmp_path, "portfolio_attribution", payload, {})

    assert {r.field.rsplit(".", 1)[-1]: r.unit for r in ledger._evidence} == {
        "income_capital_return_ars": "money",
        "portfolio_return_pct": "ratio",
    }


@pytest.mark.parametrize(
    ("path", "dimension"),
    [
        ("data.financials.totalDebt", None),
        ("data.key_stats.enterpriseValue", None),
        ("data.financials.revenueGrowth", "ratio"),
        ("data.financials.returnOnEquity", "ratio"),
        ("data.key_stats.enterpriseToEbitda", "ratio"),
        ("data.key_stats.sharesOutstanding", "count"),
        ("data.rows[0].daily_change_pct", "ratio"),
        ("risk.weights.YPFD", "ratio"),
        ("data.key_stats.PE", "ratio"),
        ("data.quote.volume", "count"),
        ("data.rows[0].bond_coupon_ars", None),
        ("data.rows[0].net_return_amount", None),
    ],
)
def test_leaf_dimension_reads_the_name_as_a_unit(path: str, dimension: str | None) -> None:
    from src.agent.grounding.evidence import _leaf_dimension

    assert _leaf_dimension(path) == dimension
