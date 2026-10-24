"""Tests for the BYMA cash ledger and T+1/T+2 funds availability."""

from datetime import date

import pytest

from backtest.engines.argentina_cash import ArgentinaCashLedger


SESSIONS = [
    date(2024, 5, 24),
    date(2024, 5, 27),
    date(2024, 5, 28),
    date(2024, 5, 29),
    date(2024, 5, 30),
]


def test_sale_proceeds_cannot_fund_same_day_purchase():
    ledger = ArgentinaCashLedger(1000, "ARS")
    ledger.buy("YPFD.BA", 5, 100, 0, date(2024, 5, 27), SESSIONS)
    assert ledger.settled_cash == 500
    assert ledger.sell("YPFD.BA", 5, 120, 0, date(2024, 5, 27), SESSIONS) == date(2024, 5, 28)
    assert ledger.pending_cash == 600
    with pytest.raises(ValueError, match="insufficient settled cash"):
        ledger.buy("GGAL.BA", 6, 100, 0, date(2024, 5, 27), SESSIONS)
    ledger.advance(date(2024, 5, 28))
    assert ledger.settled_cash == 1100
    assert ledger.pending_cash == 0


def test_t2_before_transition():
    ledger = ArgentinaCashLedger(1000, "ARS")
    ledger.buy("YPFD.BA", 1, 100, 0, date(2024, 5, 24), SESSIONS)
    assert ledger.sell("YPFD.BA", 1, 120, 1, date(2024, 5, 24), SESSIONS) == date(2024, 5, 28)
    ledger.advance(date(2024, 5, 27))
    assert ledger.pending_cash == 119
    ledger.advance(date(2024, 5, 28))
    assert ledger.pending_cash == 0
    assert ledger.settled_cash == 1019


def test_no_short_selling_or_negative_cash():
    ledger = ArgentinaCashLedger(100, "ARS")
    with pytest.raises(ValueError, match="short sales"):
        ledger.sell("YPFD.BA", 1, 100, 0, date(2024, 5, 27), SESSIONS)
    with pytest.raises(ValueError, match="insufficient settled cash"):
        ledger.buy("YPFD.BA", 2, 100, 0, date(2024, 5, 27), SESSIONS)
    assert ledger.positions == {}
    assert ledger.settled_cash == 100


def test_failed_trade_does_not_mutate_positions():
    ledger = ArgentinaCashLedger(1000, "ARS")
    with pytest.raises(ValueError, match="absent"):
        ledger.buy("YPFD.BA", 1, 100, 0, date(2024, 5, 26), SESSIONS)
    assert ledger.positions == {}
    assert ledger.settled_cash == 1000


def test_time_cannot_go_backwards():
    ledger = ArgentinaCashLedger(1000, "USD")
    ledger.advance(date(2024, 5, 28))
    with pytest.raises(ValueError, match="backwards"):
        ledger.advance(date(2024, 5, 27))
