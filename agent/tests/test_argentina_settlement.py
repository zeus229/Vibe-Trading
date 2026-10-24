"""BYMA normal settlement calendar and transition tests."""

from datetime import date

import pytest

from backtest.engines.argentina_settlement import normal_cash_lag, settlement_date


def test_2024_transition():
    assert normal_cash_lag(date(2024, 5, 24)) == 2
    assert normal_cash_lag(date(2024, 5, 27)) == 1


def test_holiday_is_not_counted_as_settlement_session():
    sessions = [
        date(2024, 5, 24),
        date(2024, 5, 27),
        date(2024, 5, 29),  # 28 omitted intentionally: fixture closure
        date(2024, 5, 30),
    ]
    assert settlement_date(date(2024, 5, 24), sessions) == date(2024, 5, 29)
    assert settlement_date(date(2024, 5, 27), sessions) == date(2024, 5, 29)


def test_unverified_or_insufficient_calendar_fails_closed():
    with pytest.raises(ValueError, match="absent"):
        settlement_date(date(2024, 5, 28), [date(2024, 5, 27), date(2024, 5, 29)])
    with pytest.raises(ValueError, match="does not cover"):
        settlement_date(date(2024, 5, 27), [date(2024, 5, 27)])
    with pytest.raises(ValueError, match="strictly increasing"):
        settlement_date(date(2024, 5, 27), [date(2024, 5, 29), date(2024, 5, 27)])
    with pytest.raises(ValueError, match="verified"):
        settlement_date(date(2024, 5, 27), [])


def test_invalid_lag_rejected():
    with pytest.raises(ValueError, match="nonnegative"):
        settlement_date(date(2024, 5, 27), [date(2024, 5, 27)], lag=-1)
