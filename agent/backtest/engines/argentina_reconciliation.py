"""Read-only BYMA settlement reconciliation for the existing BaseEngine fill ledger.

BaseEngine.capital recognizes sale proceeds on execution. Those proceeds
are not withdrawable until settlement, but can fund a buy settling on or
after the sale (same currency and settlement pool). Determine buying power
for the destination settlement date without posting cash twice.

This adapter is independent of brokers, Asistente Casa, and private APIs.
It is deliberately not wired into runner: the order planner also needs to
honor spendable cash before .BA execution can be enabled.
"""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Iterable

from backtest.engines.argentina_settlement import settlement_date


def _session_date(timestamp: object) -> date:
    if type(timestamp) is date:
        return timestamp
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is not None:
            raise ValueError("BYMA fill timestamps must be exchange-local session dates")
        return timestamp.date()
    raise ValueError("BYMA fill requires an exchange-local date/datetime")


def pending_sale_proceeds(
    fills: Iterable[object], as_of: date, settlement_sessions: Iterable[date],
) -> float:
    """Return not-yet-settled net sale proceeds, using execution evidence.

    All fills are assumed to belong to ONE currency pool. Callers must
    validate instrument currencies and prevent cross-currency aggregation.
    Closing actions are 'reduce' and 'close'; opening actions never credit cash.
    """
    if type(as_of) is not date:
        raise ValueError("as_of must be an exchange-local session date")
    sessions = tuple(settlement_sessions)
    if not sessions or any(type(day) is not date for day in sessions):
        raise ValueError("verified BYMA settlement calendar required")
    if sessions != tuple(sorted(set(sessions))) or as_of not in sessions:
        raise ValueError("settlement calendar must be sorted and include as_of")
    pending = 0.0
    for fill in fills:
        action = fill.action
        if action not in ("open", "increase", "reduce", "close"):
            raise ValueError("unknown BYMA fill action")
        trade_date = _session_date(fill.timestamp)
        if trade_date > as_of:
            raise ValueError("future fill cannot be used to value present cash")
        if action not in ("reduce", "close"):
            continue
        qty = float(fill.signed_quantity)
        price = float(fill.execution_price)
        fee = float(fill.fee)
        if not all(isfinite(value) for value in (qty, price, fee)):
            raise ValueError("non-finite BYMA fill")
        if qty >= 0 or price <= 0 or fee < 0:
            raise ValueError("BYMA sale must have negative quantity and valid price/fee")
        amount = -qty * price - fee
        if amount < 0 or not isfinite(amount):
            raise ValueError("BYMA sale proceeds cannot be negative")
        if settlement_date(trade_date, sessions) > as_of:
            pending += amount
    return pending


def available_settled_capital(
    recognized_capital: float,
    fills: Iterable[object],
    as_of: date,
    settlement_sessions: Iterable[date],
) -> float:
    """Spendable capital without double-posting BaseEngine's recognized sales.

    The existing capital remains the accounting/equity total; only buying
    power is reduced by outstanding receivables. A negative return means
    previously executed purchases already exceeded settled buying power.
    """
    amount = float(recognized_capital)
    if not isfinite(amount):
        raise ValueError("recognized capital must be finite")
    return amount - pending_sale_proceeds(fills, as_of, settlement_sessions)



def available_for_settlement(
    recognized_capital: float,
    fills: Iterable[object],
    as_of: date,
    purchase_settlement: date,
    settlement_sessions: Iterable[date],
) -> float:
    """Buying power for a dated purchase, net of later-settling receivables.

    Sales settling on or before the purchase's due date are usable for
    matched-date settlement even if not yet withdrawable. Other receivables
    are excluded. All fills must be from the same currency/clearing account.
    Callers must account for broker permissions and clearing guarantees.
    """
    capital = float(recognized_capital)
    if not isfinite(capital):
        raise ValueError("recognized capital must be finite")
    if type(as_of) is not date or type(purchase_settlement) is not date:
        raise ValueError("settlement dates must be date objects")
    sessions = tuple(settlement_sessions)
    if not sessions or any(type(day) is not date for day in sessions):
        raise ValueError("verified settlement calendar required")
    if sessions != tuple(sorted(set(sessions))) or as_of not in sessions or purchase_settlement not in sessions:
        raise ValueError("trade and purchase settlement must be in the verified calendar")
    if purchase_settlement < as_of:
        raise ValueError("purchase settlement cannot precede trade date")
    unavailable = 0.0
    for fill in fills:
        if fill.action not in ("open", "increase", "reduce", "close"):
            raise ValueError("unknown BYMA fill action")
        traded = _session_date(fill.timestamp)
        if traded > as_of:
            raise ValueError("future fill cannot fund current purchases")
        if fill.action not in ("reduce", "close"):
            continue
        qty = float(fill.signed_quantity)
        price = float(fill.execution_price)
        fee = float(fill.fee)
        if not all(isfinite(v) for v in (qty, price, fee)) or qty >= 0 or price <= 0 or fee < 0:
            raise ValueError("invalid BYMA sale fill")
        proceeds = -qty * price - fee
        if not isfinite(proceeds) or proceeds < 0:
            raise ValueError("negative or non-finite BYMA sale proceeds")
        if settlement_date(traded, sessions) > purchase_settlement:
            unavailable += proceeds
    return capital - unavailable
