"""Deterministic BYMA BaseEngine cash reconciliation tests (no external data)."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from backtest.engines.argentina_reconciliation import (
    available_settled_capital,
    pending_sale_proceeds,
    available_for_settlement,
)


SESSIONS = (
    date(2024, 5, 24),
    date(2024, 5, 27),
    date(2024, 5, 28),
    date(2024, 5, 29),
    date(2024, 5, 30),
)


def fill(action, when, quantity, price, fee=0):
    return SimpleNamespace(
        action=action, timestamp=datetime.combine(when, datetime.min.time()),
        signed_quantity=quantity, execution_price=price, fee=fee,
    )


def test_opening_fills_do_not_create_cash_receivables():
    fills = [fill("open", date(2024, 5, 27), 5, 100)]
    assert available_settled_capital(500, fills, date(2024, 5, 27), SESSIONS) == 500


def test_sale_recognized_immediately_but_not_spendable_until_settlement():
    fills = [
        fill("open", date(2024, 5, 27), 5, 100),
        fill("close", date(2024, 5, 27), -5, 120, 1),
    ]
    # BaseEngine capital after buy 500 and sale 599 equals 1099.
    assert pending_sale_proceeds(fills, date(2024, 5, 27), SESSIONS) == 599
    assert available_settled_capital(1099, fills, date(2024, 5, 27), SESSIONS) == 500
    assert available_settled_capital(1099, fills, date(2024, 5, 28), SESSIONS) == 1099


def test_pre_change_t2_with_partial_reduction_and_full_close():
    fills = [
        fill("open", date(2024, 5, 24), 3, 100),
        fill("reduce", date(2024, 5, 24), -1, 110, 2),
        fill("close", date(2024, 5, 24), -2, 120, 3),
    ]
    assert pending_sale_proceeds(fills, date(2024, 5, 27), SESSIONS) == 345
    assert pending_sale_proceeds(fills, date(2024, 5, 28), SESSIONS) == 0


def test_rejects_holiday_future_fills_and_invalid_sale_evidence():
    sale = fill("close", date(2024, 5, 27), -2, 120)
    with pytest.raises(ValueError, match="include as_of"):
        pending_sale_proceeds([sale], date(2024, 5, 26), SESSIONS)
    with pytest.raises(ValueError, match="future fill"):
        pending_sale_proceeds([sale], date(2024, 5, 24), SESSIONS)
    with pytest.raises(ValueError, match="negative quantity"):
        pending_sale_proceeds([fill("close", date(2024, 5, 27), 2, 120)], date(2024, 5, 27), SESSIONS)


def test_fails_closed_on_missing_future_settlement_date():
    with pytest.raises(ValueError, match="does not cover settlement"):
        pending_sale_proceeds(
            [fill("close", date(2024, 5, 30), -1, 100)],
            date(2024, 5, 30), SESSIONS,
        )


def test_sale_ypf_can_fund_ggal_with_matching_t1_settlement():
    """Normal T+1 sale and buy can net, without immediate withdrawal."""
    sold_ypf = [fill("close", date(2024, 5, 27), -5, 120, 1)]
    due = date(2024, 5, 28)
    assert available_for_settlement(599, sold_ypf, date(2024, 5, 27), due, SESSIONS) == 599
    assert available_settled_capital(599, sold_ypf, date(2024, 5, 27), SESSIONS) == 0


def test_t2_sale_cannot_fund_an_earlier_settling_buy():
    """A sale settling T+2 must not fund a T+1 or T+0 purchase."""
    sold_ypf = [fill("close", date(2024, 5, 24), -5, 120)]
    assert available_for_settlement(
        600, sold_ypf, date(2024, 5, 24), date(2024, 5, 27), SESSIONS,
    ) == 0
    assert available_for_settlement(
        600, sold_ypf, date(2024, 5, 24), date(2024, 5, 28), SESSIONS,
    ) == 600


def test_t1_sale_cannot_fund_same_day_cash_settlement():
    sold_ypf = [fill("close", date(2024, 5, 27), -2, 100)]
    assert available_for_settlement(
        200, sold_ypf, date(2024, 5, 27), date(2024, 5, 27), SESSIONS,
    ) == 0
