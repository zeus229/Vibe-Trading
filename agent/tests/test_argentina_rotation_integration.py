"""Full BaseEngine execution-loop regression for BYMA settlement-matched rotation.

These use synthetic OHLCV and a declared exchange calendar, never PPI,
Asistente Casa, external services, or user-specific portfolio positions.
"""

from datetime import date

import pandas as pd
import pytest

from backtest.engines.argentina_equity import ArgentinaEquityEngine


SESSIONS = tuple(date(2024, 5, d) for d in (27, 28, 29, 30, 31))


def _engine(*, adjustment="hold", cash=1000):
    return ArgentinaEquityEngine({
        "initial_cash": cash,
        "position_adjustment": adjustment,
        "ar_instruments": {
            symbol: {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.01"}
            for symbol in ("YPFD.BA", "GGAL.BA", "PAMP.BA")
        },
        "ar_settlement_sessions": SESSIONS,
        "ar_brokerage_rate": 0,
        "ar_exchange_fee_rate": 0,
        "ar_fixed_fee": 0,
        "ar_slippage": 0,
    })


def _execute(engine, targets):
    dates = pd.DatetimeIndex(["2024-05-27", "2024-05-28", "2024-05-29"])
    codes = list(targets)
    frames = {
        code: pd.DataFrame({"open": [100.0] * 3, "close": [100.0] * 3},
                           index=dates)
        for code in codes
    }
    close = pd.DataFrame({code: [100.0] * 3 for code in codes}, index=dates)
    weights = pd.DataFrame(targets, index=dates)
    engine._execute_bars(dates, frames, close, weights, codes)
    return engine


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_ypf_to_ggal_rotation_nets_t1_proceeds(mode):
    engine = _execute(_engine(adjustment=mode), {
        "GGAL.BA": [0.0, 1.0, 1.0],  # deliberately first in input order
        "YPFD.BA": [1.0, 0.0, 0.0],
    })
    fills = [(f.symbol, f.action, f.timestamp.date(), f.signed_quantity)
             for f in engine.fill_records]
    assert ("YPFD.BA", "open", date(2024, 5, 27), 10) in fills
    assert ("YPFD.BA", "close", date(2024, 5, 28), -10) in fills
    assert ("GGAL.BA", "open", date(2024, 5, 28), 10) in fills
    assert engine.equity_snapshots[-1].equity == pytest.approx(1000)


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_simultaneous_orders_do_not_double_spend_proceeds(mode):
    engine = _execute(_engine(adjustment=mode), {
        "GGAL.BA": [0, 0.6, 0.6],
        "PAMP.BA": [0, 0.6, 0.6],
        "YPFD.BA": [1, 0, 0],
    })
    rotated = [
        f for f in engine.fill_records
        if f.action in ("open", "increase")
        and f.timestamp.date() == date(2024, 5, 28)
        and f.symbol in ("GGAL.BA", "PAMP.BA")
    ]
    assert rotated
    assert sum(f.signed_quantity * f.execution_price + f.fee for f in rotated) <= 1000 + 1e-9
    assert engine.equity_snapshots[-1].equity == pytest.approx(1000)


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_rotation_includes_all_trade_fees_in_final_equity(mode):
    engine = _execute(_engine(adjustment=mode, fee=5), {
        "GGAL.BA": [0, 1, 1],
        "YPFD.BA": [1, 0, 0],
    })
    # Four fills: buy YPF, sell YPF, buy GGAL, terminal sell GGAL.
    # Each fill incurs a configured 5 ARS fee and prices stay flat.
    assert [(f.symbol, f.action) for f in engine.fill_records] == [
        ("YPFD.BA", "open"),
        ("YPFD.BA", "close"),
        ("GGAL.BA", "open"),
        ("GGAL.BA", "close"),
    ]
    assert engine.equity_snapshots[-1].equity == pytest.approx(980)
    assert engine.capital == pytest.approx(980)
