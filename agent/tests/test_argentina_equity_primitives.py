"""BYMA execution primitive tests. No claims about complete total-return backtesting."""

import pytest
import pandas as pd

from backtest.engines.argentina_equity import ArgentinaEquityEngine


@pytest.fixture
def config():
    return {
        "ar_instruments": {
            "YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"},
            "AAPL.BA": {"kind": "cedear", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"},
        },
        "ar_brokerage_rate": 0.001,
        "ar_exchange_fee_rate": 0.0001,
        "ar_fixed_fee": 5,
    }


def test_share_and_cedear_long_only(config):
    engine = ArgentinaEquityEngine(config)
    bar = pd.Series({"close": 100.0})
    for code in ("YPFD.BA", "AAPL.BA"):
        assert engine.can_execute(code, 1, bar)
        assert engine.can_execute(code, 0, bar)
        assert not engine.can_execute(code, -1, bar)


def test_lot_rounding_and_costs(config):
    engine = ArgentinaEquityEngine(config)
    engine._active_symbol = "YPFD.BA"
    assert engine.round_size(3.9, 100) == 3
    assert engine.calc_commission(10, 100, 1, True) == pytest.approx(6.1)


def test_tick_rounding_against_trader(config):
    engine = ArgentinaEquityEngine(config)
    engine._active_symbol = "AAPL.BA"
    assert engine.apply_slippage(100.021, 1) == pytest.approx(100.05)
    assert engine.apply_slippage(100.021, 0) == pytest.approx(100.0)


def test_unknown_instrument_fails_closed(config):
    engine = ArgentinaEquityEngine(config)
    with pytest.raises(ValueError, match="unregistered"):
        engine.can_execute("AL30.BA", 1, pd.Series({"close": 100}))


def test_missing_metadata_and_mixed_currency_fail_closed(config):
    bad = dict(config)
    bad["ar_instruments"] = {"YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1}}
    with pytest.raises(ValueError, match="tick_size"):
        ArgentinaEquityEngine(bad)
    bad["ar_instruments"] = {
        **config["ar_instruments"],
        "GGALD.BA": {"kind": "share", "currency": "USD", "lot_size": 1, "tick_size": "0.01"},
    }
    with pytest.raises(ValueError, match="mixed ARS/USD"):
        ArgentinaEquityEngine(bad)


@pytest.mark.parametrize("key,value", [("ar_slippage", -0.1), ("ar_fixed_fee", float("nan")),
                                        ("ar_brokerage_rate", float("inf"))])
def test_invalid_costs_rejected(config, key, value):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        ArgentinaEquityEngine({**config, key: value})
