"""BYMA settlement-date calculation with explicit exchange calendars.

This is a date-rule helper, NOT a cash settlement ledger. An authoritative
calendar must be supplied by the caller; Python weekday arithmetic alone is
not sufficient for BYMA holidays or exceptional market closures.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date
from typing import Iterable


# Normal cash settlement: T+2 before the 2024-05-27 effective change,
# T+1 starting 2024-05-27. Do not apply to other settlement modalities.
NORMAL_CASH_CYCLES = (
    (date.min, 2),
    (date(2024, 5, 27), 1),
)


def normal_cash_lag(trade_date: date) -> int:
    """Settlement lag in exchange business sessions, versioned by trade date."""
    if not isinstance(trade_date, date):
        raise TypeError("trade_date must be a date")
    effective = [start for start, _ in NORMAL_CASH_CYCLES]
    return NORMAL_CASH_CYCLES[bisect_right(effective, trade_date) - 1][1]


def settlement_date(
    trade_date: date,
    settlement_sessions: Iterable[date],
    *,
    lag: int | None = None,
) -> date:
    """Resolve settlement against a supplied, verified session calendar.

    Calendar must include the trade date and enough future sessions.
    A trade date absent from the calendar fails closed (holiday/closure).
    """
    if not isinstance(trade_date, date):
        raise TypeError("trade_date must be a date")
    sessions = list(settlement_sessions)
    if not sessions or any(not isinstance(day, date) for day in sessions):
        raise ValueError("verified settlement calendar required")
    if sessions != sorted(set(sessions)):
        raise ValueError("settlement calendar must be strictly increasing")
    if trade_date not in sessions:
        raise ValueError("trade date absent from settlement calendar")
    days = normal_cash_lag(trade_date) if lag is None else lag
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("settlement lag must be a nonnegative integer")
    target = sessions.index(trade_date) + days
    if target >= len(sessions):
        raise ValueError("settlement calendar does not cover settlement date")
    return sessions[target]
