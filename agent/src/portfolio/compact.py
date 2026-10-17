"""Small ARS composition projection of one existing immutable snapshot."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


class CompactPortfolioError(ValueError):
    """The snapshot cannot supply a complete, unambiguous ARS projection."""


def _amount(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompactPortfolioError(f"Missing numeric snapshot field: {field}")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise CompactPortfolioError(f"Invalid snapshot field: {field}") from exc
    if not number.is_finite() or number < 0:
        raise CompactPortfolioError(f"Invalid snapshot field: {field}")
    return number


def _signed_amount(value: Any, field: str) -> Decimal:
    """Like ``_amount`` but allows negatives: signed pending settlement is legitimately below zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompactPortfolioError(f"Missing numeric snapshot field: {field}")
    number = Decimal(str(value))
    if not number.is_finite():
        raise CompactPortfolioError(f"Invalid snapshot field: {field}")
    return number


def compact_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Project all holdings and explicit cash from exactly one snapshot.

    Only ARS-native portfolios are supported: no FX, refresh or other producer
    is consulted. Residuals retain their persisted unpriced/other classification.
    Country buckets cover holdings only; cash has no inferred country.
    """
    if not snapshot.get("snapshot_id") or not snapshot.get("created_at"):
        raise CompactPortfolioError("Snapshot identity is missing")
    totals = snapshot.get("totals", {})
    if set(totals.get("native_by_currency", {})) != {"ARS"} or any(
        _amount(totals.get(key), key) != 0 for key in ("usd", "cny")
    ):
        raise CompactPortfolioError("Compact view requires an exclusively ARS-native snapshot")
    total = _amount(totals["native_by_currency"]["ARS"], "total ARS")
    cash = unsettled = other = account_total = Decimal(0)
    breakdown: dict[str, Decimal] | None = {}
    for account in snapshot.get("accounts", []):
        if account.get("status") != "ok":
            continue  # Same exclusion of failed sources as the stored totals.
        if account.get("native_currency") != "ARS":
            raise CompactPortfolioError("Account has no supported ARS valuation")
        account_total += _amount(account.get("total_native"), "total_native")
        cash += _amount(account.get("cash_native"), "cash_native")
        unsettled += _amount(account.get("unsettled_cash_native"), "unsettled_cash_native")
        other += _amount(account.get("unpriced_or_other_native"), "unpriced_or_other_native")
        declared = account.get("cash_semantics")
        if breakdown is not None and isinstance(declared, dict):
            for key in ("settled_cash_ars", "unsettled_signed_ars", "economic_cash_ars"):
                breakdown[key] = breakdown.get(key, Decimal(0)) + _signed_amount(declared.get(key), key)
        elif account.get("cash_native") or account.get("unsettled_cash_native"):
            breakdown = None  # Mixed or legacy sources: no partial breakdown is better than a wrong one.

    countries: dict[str, Decimal] = {}
    types = {key: Decimal(0) for key in ("ACCIONES", "CEDEARS", "BONOS", "FCI", "EFECTIVO")}
    holdings = []
    invested = Decimal(0)
    for row in snapshot.get("positions", []):
        if row.get("native_currency") != "ARS" or row.get("priced") is not True:
            raise CompactPortfolioError("A holding lacks a priced ARS valuation")
        symbol = row.get("symbol")
        instrument_type = row.get("source_instrument_type")
        if not symbol or not instrument_type:
            raise CompactPortfolioError("A holding lacks canonical symbol/instrument type")
        value = _amount(row.get("market_value_native"), "market_value_native")
        country = row.get("country") or "Unclassified"
        invested += value
        countries[country] = countries.get(country, Decimal(0)) + value
        types[instrument_type] = types.get(instrument_type, Decimal(0)) + value
        holdings.append(
            {
                "symbol": symbol,
                "instrument_type": instrument_type,
                "country": row.get("country"),
                "market_value_ars": float(value),
                "native_currency": "ARS",
                "weight_portfolio": float((value / total).quantize(Decimal("0.00000001"))) if total else 0.0,
            }
        )
    # Allow only the snapshot producer's 8-decimal rounding noise. Never turn
    # the difference into cash or silently adjust the canonical denominator.
    if abs(account_total - total) > Decimal("0.00001") or abs(invested + cash + unsettled + other - total) > Decimal(
        "0.00001"
    ):
        raise CompactPortfolioError("Snapshot components do not reconcile with its total")
    types["EFECTIVO"] += cash + unsettled

    def buckets(values: dict[str, Decimal]) -> dict[str, Any]:
        return {
            key: {"market_value_ars": float(value), "weight": float(value / total) if total else 0.0}
            for key, value in values.items()
        }

    cash_breakdown = (
        {key: float(value) for key, value in breakdown.items()}
        if breakdown and len(breakdown) == 3
        else None
    )
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "as_of": snapshot["created_at"],
        "valuation_version": snapshot.get("valuation_version"),
        "complete": snapshot["complete"],
        "native_currency": "ARS",
        "totals": {
            "portfolio_market_value_ars": float(total),
            "cash_ars": float(cash),
            "unsettled_cash_ars": float(unsettled),
            "unpriced_or_other_ars": float(other),
            # Declared by the connector: settled cash, SIGNED pending settlement and their sum. When a negative
            # pending flow was folded into ``cash_ars``, only this block says which part is really settled.
            "cash_breakdown_ars": cash_breakdown,
        },
        "aggregates": {"by_country": buckets(countries), "by_instrument_type": buckets(types)},
        "holdings": holdings,
        "semantics": "Country buckets cover holdings only. EFECTIVO includes settled and unsettled cash. When totals.cash_breakdown_ars is present it is authoritative: settled_cash_ars is immediately available, unsettled_signed_ars is signed pending settlement (negative = purchases not yet debited) and economic_cash_ars is their sum; cash_ars may then hold economic cash, so never call it settled. Total includes holdings, both cash components and persisted unpriced/other. Weights use total portfolio value.",
    }
