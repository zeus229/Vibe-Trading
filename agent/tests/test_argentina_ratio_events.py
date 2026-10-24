"""CEDEAR ratio changes across the ex-date -> credit-date window.

Dates are the issuer's (Banco Comafi, issuer of these CEDEAR programs):
- 2020: notice to BCBA 2020-10-16, RESFC-2020-20830-APN-DIR#CNV, 39 programs
  (MSFT 5:1 -> 10:1, GOOGL 29:1 -> 58:1, AMZN 72:1 -> 144:1, NVDA 12:1 -> 24:1,
  MELI 2:1 -> 60:1). Ex 2020-10-22, record 10-23, credit 10-26.
- 2022: notice 2022-11-03, RESFC-2022-21967-APN-DIR#CNV, 33 programs
  (MSFT 10:1 -> 30:1). Ex 2022-11-17, record 11-18, credit 11-22.
- 2024: notice to BCBA 2024-01-08, RESFC-2023-22529-APN-DIR#CNV, 30 programs
  (AAPL 10:1 -> 20:1, MELI 60:1 -> 120:1). Ex 2024-01-24, record 01-25,
  credit 01-26.
Prices are synthetic; only the dates and ratios come from those notices.
"""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from backtest.engines.argentina_equity import ArgentinaEquityEngine

SHARE = {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"}


def _cedear(underlying, old, new, ex, credit, tick="0.5"):
    return {
        "kind": "cedear", "currency": "ARS", "lot_size": 1, "tick_size": tick,
        "underlying": underlying,
        "ratio_history": [
            {"effective_date": "2019-01-02", "ratio": str(old)},
            {"effective_date": ex, "ratio": str(new), "credit_date": credit},
        ],
    }


AAPL_2024 = _cedear("AAPL", 10, 20, "2024-01-24", "2024-01-26")
MSFT_2020 = _cedear("MSFT", 5, 10, "2020-10-22", "2020-10-26")
MSFT_2022 = _cedear("MSFT", 10, 30, "2022-11-17", "2022-11-22")

S2024 = [date(2024, 1, d) for d in (19, 22, 23, 24, 25, 26, 29, 30, 31)]
S2020 = [date(2020, 10, d) for d in (19, 20, 21, 22, 23, 26, 27, 28, 29)]
# 2022-11-21 was a holiday (Día de la Soberanía Nacional, moved).
S2022 = [date(2022, 11, d) for d in (14, 15, 16, 17, 18, 22, 23, 24, 25)]


def _bars(days, pre_price, factor, ex, volume=1000.0):
    """Unadjusted series: the price steps by *factor* on the ex-date."""
    ex_day = pd.Timestamp(ex)
    prices = [pre_price if pd.Timestamp(d) < ex_day else pre_price * factor for d in days]
    frame = pd.DataFrame(
        {"open": prices, "high": prices, "low": prices, "close": prices,
         "volume": [volume] * len(days)},
        index=pd.DatetimeIndex([pd.Timestamp(d) for d in days]),
    )
    frame.attrs["quote_currency"] = "ARS"
    return frame


class _Loader:
    def __init__(self, frames):
        self.frames = frames

    def fetch(self, *args, **kwargs):
        out = {}
        for code, frame in self.frames.items():
            out[code] = frame.copy()
            out[code].attrs = dict(frame.attrs)
        return out


class _Signal:
    def __init__(self, weights):
        self.weights = weights

    def generate(self, data_map):
        return {code: pd.Series(self.weights[code], index=df.index)
                for code, df in data_map.items()}


def _run(code, instrument, sessions, frame, weights, tmp_path, mode="hold", **extra):
    engine = ArgentinaEquityEngine({
        "initial_cash": 100_000, "position_adjustment": mode,
        "ar_instruments": {code: instrument},
        "ar_settlement_sessions": [d.isoformat() for d in sessions],  # JSON form
        **extra,
    })
    metrics = engine.run_backtest(
        {"codes": [code], "start_date": str(frame.index[0].date()),
         "end_date": str(frame.index[-1].date()), "initial_cash": 100_000,
         "source": "local"},
        _Loader({code: frame}), _Signal({code: weights}), tmp_path,
    )
    fills = [(f.action, f.timestamp.date(), f.signed_quantity, f.reason)
             for f in engine.fill_records]
    return engine, metrics, fills


DAYS_2024 = S2024[:7]  # 01-19 .. 01-29


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_2024_ex_date_sale_settles_t2_on_the_credit_date(tmp_path, mode):
    """January 2024 was still T+2 (T+1 began 2024-05-27): an ex-date sale on
    01-24 settles 01-26, the credit date, so it is deliverable."""
    frame = _bars(DAYS_2024, 25000.0, 0.5, "2024-01-24")
    # Signal on bar i executes at bar i+1: buy 01-22, exit requested from 01-24.
    weights = [1, 1, 0, 0, 0, 0, 0]
    engine, metrics, fills = _run("AAPL.BA", AAPL_2024, S2024, frame, weights, tmp_path, mode)
    assert [(a, d) for a, d, _, _ in fills] == [
        ("open", date(2024, 1, 22)), ("close", date(2024, 1, 24)),
    ]
    # Flat underlying value: no artificial loss from the 50% unit change.
    assert metrics["total_return"] == pytest.approx(0.0, abs=1e-12)


def test_2024_purchase_on_ex_date_follows_normal_settlement(tmp_path):
    """Bought on the ex-date in new units: only normal T+2 settlement applies."""
    frame = _bars(DAYS_2024, 25000.0, 0.5, "2024-01-24")
    weights = [0, 0, 1, 0, 0, 0, 0]  # buy 01-24, exit requested from 01-25
    engine, metrics, fills = _run("AAPL.BA", AAPL_2024, S2024, frame, weights, tmp_path)
    assert [(a, d) for a, d, _, _ in fills] == [
        ("open", date(2024, 1, 24)), ("close", date(2024, 1, 26)),
    ]


# Synthetic (NOT an official event): a T+1-era change with the issuer's usual
# ex -> credit spacing of two sessions, to exercise the window where a T+1
# sale would settle before the additional certificates exist.
SYN_SESSIONS = [date(2025, 3, d) for d in (17, 18, 19, 20, 21, 24, 25, 26)]
SYN = _cedear("SYN", 10, 20, "2025-03-19", "2025-03-21")


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_t1_sale_before_credit_is_refused_until_it_settles_on_credit(tmp_path, mode):
    frame = _bars(SYN_SESSIONS[:7], 25000.0, 0.5, "2025-03-19")
    # Signal on bar i executes at bar i+1: buy 03-18, exit requested from 03-19.
    weights = [1, 0, 0, 0, 0, 0, 0]
    engine, metrics, fills = _run("SYN.BA", SYN, SYN_SESSIONS, frame, weights, tmp_path, mode)
    # 03-19 would settle 03-20 < credit 03-21: refused; 03-20 settles 03-21.
    assert [(a, d) for a, d, _, _ in fills] == [
        ("open", date(2025, 3, 18)), ("close", date(2025, 3, 20)),
    ]
    assert metrics["total_return"] == pytest.approx(0.0, abs=1e-12)


def test_t1_run_ending_before_credit_retains_the_position(tmp_path):
    frame = _bars(SYN_SESSIONS[:3], 25000.0, 0.5, "2025-03-19")  # ends on ex
    engine, metrics, fills = _run("SYN.BA", SYN, SYN_SESSIONS, frame, [1, 1, 1], tmp_path)
    assert [a for a, *_ in fills] == ["open"]
    assert metrics["ar_unliquidated_position_count"] == 1
    assert metrics["total_return"] == pytest.approx(0.0, abs=1e-12)


def test_t1_purchase_after_ex_date_is_not_held_back(tmp_path):
    frame = _bars(SYN_SESSIONS[:7], 25000.0, 0.5, "2025-03-19")
    weights = [0, 1, 0, 0, 0, 0, 0]  # buy 03-19 (ex), exit requested 03-20
    engine, metrics, fills = _run("SYN.BA", SYN, SYN_SESSIONS, frame, weights, tmp_path)
    assert [(a, d) for a, d, _, _ in fills] == [
        ("open", date(2025, 3, 19)), ("close", date(2025, 3, 20)),
    ]


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_2020_t2_sale_on_ex_date_settles_on_credit_and_is_allowed(tmp_path, mode):
    """Under T+2 (pre-2024-05-27) an ex-date sale settles 10-26 = credit."""
    days = S2020[:7]
    frame = _bars(days, 7690.0, 0.5, "2020-10-22")
    weights = [1, 1, 0, 0, 0, 0, 0]  # buy 10-20, exit requested from 10-22
    engine, metrics, fills = _run("MSFT.BA", MSFT_2020, S2020, frame, weights, tmp_path, mode)
    assert [(a, d) for a, d, _, _ in fills] == [
        ("open", date(2020, 10, 20)), ("close", date(2020, 10, 22)),
    ]
    assert metrics["total_return"] == pytest.approx(0.0, abs=1e-12)


def test_2022_holiday_between_record_and_credit_is_respected(tmp_path):
    """Ex 11-17 (T+2 -> 11-22 because 11-21 is closed) settles on the credit."""
    days = S2022[:7]
    frame = _bars(days, 7860.0, 1 / 3, "2022-11-17")
    weights = [1, 1, 0, 0, 0, 0, 0]  # buy 11-15, exit requested from 11-17
    engine, metrics, fills = _run("MSFT.BA", MSFT_2022, S2022, frame, weights, tmp_path)
    assert [(a, d) for a, d, _, _ in fills] == [
        ("open", date(2022, 11, 15)), ("close", date(2022, 11, 17)),
    ]
    # The real lot before the change is 3 adjusted units.
    assert fills[0][2] % 3 == 0


def test_2022_without_the_holiday_the_sale_would_be_early_and_blocked(tmp_path):
    """Same event with 11-21 wrongly listed as a session: settlement 11-21 <
    credit 11-22, so the ex-date sale is refused and happens on 11-18."""
    sessions = sorted(S2022 + [date(2022, 11, 21)])
    days = [d for d in sessions if d <= date(2022, 11, 23)]
    frame = _bars(days, 7860.0, 1 / 3, "2022-11-17")
    weights = [1, 1, 0, 0, 0, 0, 0, 0]
    engine, metrics, fills = _run("MSFT.BA", MSFT_2022, sessions, frame, weights, tmp_path)
    assert [(a, d) for a, d, _, _ in fills] == [
        ("open", date(2022, 11, 15)), ("close", date(2022, 11, 18)),
    ]


def test_ratio_event_without_credit_date_fails_closed(tmp_path):
    instrument = _cedear("AAPL", 10, 20, "2024-01-24", "2024-01-26")
    del instrument["ratio_history"][1]["credit_date"]
    frame = _bars(DAYS_2024, 25000.0, 0.5, "2024-01-24")
    with pytest.raises(ValueError, match="credit_date"):
        _run("AAPL.BA", instrument, S2024, frame, [1] * 7, tmp_path)


def test_credit_date_before_ex_date_rejected():
    instrument = _cedear("AAPL", 10, 20, "2024-01-24", "2024-01-23")
    with pytest.raises(ValueError, match="precedes"):
        ArgentinaEquityEngine({"ar_instruments": {"AAPL.BA": instrument},
                               "ar_settlement_sessions": S2024})


# ── units: lots, ticks, fees and valuation on adjusted prices ──

def _prepared(instrument=AAPL_2024, days=DAYS_2024):
    engine = ArgentinaEquityEngine({"ar_instruments": {"AAPL.BA": instrument},
                                    "ar_settlement_sessions": S2024,
                                    "ar_fixed_fee": 7, "ar_brokerage_rate": 0.005})
    engine.prepare_market_data({"AAPL.BA": _bars(days, 25000.0, 0.5, "2024-01-24")})
    engine._active_symbol = "AAPL.BA"
    return engine


def test_unit_scale_is_new_over_old_before_the_ex_date_only():
    engine = _prepared()
    assert engine.unit_scale("AAPL.BA", pd.Timestamp("2024-01-23")) == Decimal(2)
    assert engine.unit_scale("AAPL.BA", pd.Timestamp("2024-01-24")) == Decimal(1)
    assert engine.unit_scale("AAPL.BA", None) == Decimal(1)


def test_lots_are_whole_real_certificates():
    engine = _prepared()
    engine._bar_ts = pd.Timestamp("2024-01-23")
    assert engine.round_size(9.9, 12500.0) == 8.0  # 4 real certificates
    engine._bar_ts = pd.Timestamp("2024-01-24")
    assert engine.round_size(9.9, 12500.0) == 9.0


def test_ticks_map_to_the_real_price_grid():
    engine = _prepared()
    engine._bar_ts = pd.Timestamp("2024-01-23")
    # Real tick 0.5 on 25000.3 -> adjusted tick 0.25 on 12500.15.
    assert engine.apply_slippage(12500.15, 1) == pytest.approx(12500.25)
    assert engine.apply_slippage(12500.15, -1) == pytest.approx(12500.0)
    engine._bar_ts = pd.Timestamp("2024-01-24")
    assert engine.apply_slippage(12500.15, 1) == pytest.approx(12500.5)


def test_commission_is_invariant_to_the_unit_restatement():
    """8 adjusted units at 12500 are 4 real certificates at 25000: same notional,
    same proportional fee, one fixed fee per order either way."""
    engine = _prepared()
    assert engine.calc_commission(8, 12500.0, 1, True) == pytest.approx(
        engine.calc_commission(4, 25000.0, 1, True))
    assert engine.calc_commission(8, 12500.0, 1, True) == pytest.approx(100000 * 0.005 + 7)


def test_shares_and_unaffected_cedears_have_unit_scale_one():
    engine = ArgentinaEquityEngine({"ar_instruments": {"YPFD.BA": SHARE},
                                    "ar_settlement_sessions": S2024})
    assert engine.unit_scale("YPFD.BA", pd.Timestamp("2024-01-23")) == Decimal(1)
