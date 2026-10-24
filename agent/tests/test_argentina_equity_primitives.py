"""BYMA execution primitive tests. No claims about complete total-return backtesting."""

import pytest
from datetime import date
from types import SimpleNamespace
import pandas as pd

from backtest.engines.argentina_equity import ArgentinaEquityEngine, argentina_sell_is_settled, argentina_can_execute


@pytest.fixture
def config():
    return {
        "ar_instruments": {
            "YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"},
            "AAPL.BA": {"kind": "cedear", "currency": "ARS", "lot_size": 1, "tick_size": "0.05", "underlying": "AAPL", "ratio_history": [{"effective_date": "2020-01-01", "ratio": "10"}]},
        },
        "ar_brokerage_rate": 0.001,
        "ar_exchange_fee_rate": 0.0001,
        "ar_fixed_fee": 5,
    }


def test_share_and_cedear_long_only(config):
    engine = ArgentinaEquityEngine(config)
    bar = pd.Series({"close": 100.0, "volume": 1.0})
    for code in ("YPFD.BA", "AAPL.BA"):
        assert engine.can_execute(code, 1, bar)
        assert not engine.can_execute(code, 0, bar)  # no position/fill/calendar evidence
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
        engine.can_execute("AL30.BA", 1, pd.Series({"close": 100, "volume": 1.0}))


def test_invalid_metadata_and_mixed_currency_fail_closed(config):
    bad = dict(config)
    # Omitted tick_size means BYMA CIR 3569 bands; an invalid one still fails.
    bad["ar_instruments"] = {"YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1}}
    assert ArgentinaEquityEngine(bad).instruments["YPFD.BA"]["tick_size"] is None
    bad["ar_instruments"] = {"YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1,
                                         "tick_size": "-1"}}
    with pytest.raises(ValueError, match="tick_size must be a positive number"):
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


def test_cedear_requires_dated_ratio_history(config):
    info = dict(config["ar_instruments"]["AAPL.BA"])
    info.pop("ratio_history")
    with pytest.raises(ValueError, match="ratio_history"):
        ArgentinaEquityEngine({**config, "ar_instruments": {"AAPL.BA": info}})


def test_cedear_rejects_invalid_ratio_history(config):
    info = dict(config["ar_instruments"]["AAPL.BA"])
    info["ratio_history"] = [
        {"effective_date": "2024-01-01", "ratio": "10"},
        {"effective_date": "2023-01-01", "ratio": "0"},
    ]
    with pytest.raises(ValueError, match="ratio history"):
        ArgentinaEquityEngine({**config, "ar_instruments": {"AAPL.BA": info}})


def test_ratio_lookup_is_point_in_time(config):
    info = dict(config["ar_instruments"]["AAPL.BA"])
    info["ratio_history"] = [
        {"effective_date": "2020-01-01", "ratio": "10"},
        {"effective_date": "2025-06-01", "ratio": "20"},
    ]
    engine = ArgentinaEquityEngine({**config, "ar_instruments": {"AAPL.BA": info}})
    assert str(engine.ratio_at("AAPL.BA", "2024-12-31")) == "10"
    assert str(engine.ratio_at("AAPL.BA", "2025-06-01")) == "20"
    with pytest.raises(ValueError, match="no effective ratio"):
        engine.ratio_at("AAPL.BA", "2019-12-31")

def test_byma_settlement_eligibility_uses_actual_sessions(config):
    sessions = (date(2024, 5, 24), date(2024, 5, 27),
                date(2024, 5, 28), date(2024, 5, 29))
    cfg = {**config, "ar_settlement_sessions": sessions}
    engine = ArgentinaEquityEngine(cfg)
    engine.positions["YPFD.BA"] = object()
    engine.fill_records = [
        SimpleNamespace(symbol="YPFD.BA", action="open",
                        timestamp=pd.Timestamp("2024-05-24")),
    ]
    assert not argentina_sell_is_settled(engine, "YPFD.BA", date(2024, 5, 27), sessions)
    assert argentina_sell_is_settled(engine, "YPFD.BA", date(2024, 5, 28), sessions)
    assert not argentina_sell_is_settled(engine, "YPFD.BA", date(2024, 5, 26), sessions)
    assert argentina_can_execute(engine, engine, "YPFD.BA", 0,
                                  pd.Series({"close": 100, "volume": 1.0}, name=pd.Timestamp("2024-05-28")))


def test_scale_in_resets_byma_settlement_hold(config):
    sessions = tuple(date(2024, 5, day) for day in (27, 28, 29, 30))
    engine = ArgentinaEquityEngine({**config, "ar_settlement_sessions": sessions})
    engine.positions["YPFD.BA"] = object()
    engine.fill_records = [
        SimpleNamespace(symbol="YPFD.BA", action="open",
                        timestamp=pd.Timestamp("2024-05-27")),
        SimpleNamespace(symbol="YPFD.BA", action="increase",
                        timestamp=pd.Timestamp("2024-05-28")),
    ]
    assert not argentina_sell_is_settled(engine, "YPFD.BA", date(2024, 5, 28), sessions)
    assert argentina_sell_is_settled(engine, "YPFD.BA", date(2024, 5, 29), sessions)


def test_missing_settlement_evidence_fails_closed(config):
    engine = ArgentinaEquityEngine(config)
    engine.positions["YPFD.BA"] = object()
    engine.fill_records = []
    assert not engine.can_execute(
        "YPFD.BA", 0, pd.Series({"close": 100, "volume": 1.0}, name=pd.Timestamp("2024-05-28"))
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        ArgentinaEquityEngine({
            **config, "ar_settlement_sessions": [date(2024, 5, 28), date(2024, 5, 27)]
        })


def test_byma_buying_power_includes_same_settlement_sales(config):
    from backtest.engines.base import BaseEngine

    sessions = tuple(date(2024, 5, day) for day in (27, 28, 29))
    engine = ArgentinaEquityEngine({**config, "ar_settlement_sessions": sessions})
    engine.capital = 1100.0
    engine.fill_records = [
        SimpleNamespace(symbol="YPFD.BA", action="open",
                        timestamp=pd.Timestamp("2024-05-27"), signed_quantity=5,
                        execution_price=100.0, fee=0.0),
        SimpleNamespace(symbol="YPFD.BA", action="close",
                        timestamp=pd.Timestamp("2024-05-27"), signed_quantity=-5,
                        execution_price=120.0, fee=0.0),
    ]
    ts = pd.Timestamp("2024-05-27")
    assert BaseEngine.available_cash(engine, ts) == 1100.0
    assert engine.available_cash(ts) == 1100.0  # T+1 sale funds a T+1 buy
    assert engine.available_cash(pd.Timestamp("2024-05-28")) == 1100.0


def test_byma_same_bar_reductions_can_fund_matched_settlement(config):
    from backtest.engines.base import BaseEngine

    engine = ArgentinaEquityEngine({
        **config,
        "ar_settlement_sessions": [date(2024, 5, 27), date(2024, 5, 28)],
    })
    reductions = [SimpleNamespace(capital_credit=900.0)]
    ts = pd.Timestamp("2024-05-27")
    assert BaseEngine.projected_reduction_buying_power(engine, reductions, ts) == 900.0
    assert engine.projected_reduction_buying_power(reductions, ts) == 900.0
    assert engine.available_cash(ts) == engine.capital


def test_byma_no_calendar_still_fails_closed(config):
    engine = ArgentinaEquityEngine(config)
    with pytest.raises(ValueError, match="verified settlement calendar"):
        engine.available_cash(pd.Timestamp("2024-05-27"))
