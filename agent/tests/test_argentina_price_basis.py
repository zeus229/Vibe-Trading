"""BYMA price-basis validation: currency, placeholder bars, splits, CEDEAR ratios.

Deterministic: synthetic bars plus a short, inlined excerpt of public Yahoo
AAPL.BA quotes around the 2024-01-24 CEDEAR ratio change (10 -> 20), which
Yahoo's series does not adjust. No network, PPI or private service.
"""

from datetime import date

import pandas as pd
import pytest

from backtest.engines.argentina_data import (
    drop_non_session_bars,
    reconcile_ratio_changes,
    unexplained_discontinuities,
)
from backtest.engines.argentina_equity import ArgentinaEquityEngine

# (date, open, close, volume) from Yahoo's v8 chart quote series for AAPL.BA.
AAPL_BA_2024_RATIO_CHANGE = (
    ("2024-01-15", 21142.00, 21682.00, 15431),
    ("2024-01-16", 21670.00, 22202.50, 88907),
    ("2024-01-17", 22170.00, 23177.50, 63835),
    ("2024-01-18", 23300.00, 24281.50, 93380),
    ("2024-01-19", 24351.00, 25035.00, 72433),
    ("2024-01-22", 25105.00, 25414.50, 67816),
    ("2024-01-23", 25466.00, 25100.00, 28899),
    ("2024-01-24", 12550.00, 12697.50, 54161),
    ("2024-01-25", 12798.50, 12424.00, 57113),
    ("2024-01-26", 12500.00, 11997.50, 58520),
    ("2024-01-29", 11997.50, 12103.00, 123763),
    ("2024-01-30", 12105.00, 11924.50, 106131),
    ("2024-01-31", 12000.00, 11557.50, 83851),
    ("2024-02-01", 11600.00, 12031.00, 71920),
    ("2024-02-02", 11780.00, 12002.50, 111722),
)
AAPL_SESSIONS = tuple(date.fromisoformat(row[0]) for row in AAPL_BA_2024_RATIO_CHANGE) + (
    date(2024, 2, 5), date(2024, 2, 6),
)
RATIO_10_TO_20 = [
    {"effective_date": "2020-01-01", "ratio": "10"},
    {"effective_date": "2024-01-24", "ratio": "20"},
]


def _frame(rows, currency="ARS"):
    frame = pd.DataFrame(
        {
            "open": [r[1] for r in rows],
            "high": [max(r[1], r[2]) for r in rows],
            "low": [min(r[1], r[2]) for r in rows],
            "close": [r[2] for r in rows],
            "volume": [float(r[3]) for r in rows],
        },
        index=pd.DatetimeIndex([r[0] for r in rows]),
    )
    if currency:
        frame.attrs["quote_currency"] = currency
    return frame


def _aapl():
    return _frame(AAPL_BA_2024_RATIO_CHANGE)


def _flat(days, price=100.0, volume=1000.0, currency="ARS"):
    return _frame([(d, price, price, volume) for d in days], currency)


def _engine(instruments, sessions=AAPL_SESSIONS, **extra):
    return ArgentinaEquityEngine({
        "initial_cash": 100_000,
        "ar_instruments": instruments,
        "ar_settlement_sessions": sessions,
        **extra,
    })


def _cedear(history=None, currency="ARS"):
    return {"kind": "cedear", "currency": currency, "lot_size": 1, "tick_size": "0.5",
            "underlying": "AAPL", "ratio_history": history or RATIO_10_TO_20}


SHARE = {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"}


# ── ratio-change reconciliation ──

def test_real_yahoo_aapl_ba_ratio_change_is_detected_and_rescaled():
    frame, events = reconcile_ratio_changes(_aapl(), "AAPL.BA", RATIO_10_TO_20)
    (event,) = events
    assert event["source_basis"] == "unadjusted"
    assert event["observed_move"] == pytest.approx(12697.5 / 25100.0)
    # Earlier bars are put on the post-change unit (x10/20); later bars intact.
    assert frame.loc["2024-01-23", "close"] == pytest.approx(12550.0)
    assert frame.loc["2024-01-23", "volume"] == pytest.approx(2 * 28899)
    assert frame.loc["2024-01-24", "close"] == pytest.approx(12697.5)
    assert unexplained_discontinuities(frame) == []


def test_unadjusted_ratio_change_is_an_unexplained_discontinuity_if_undeclared():
    assert unexplained_discontinuities(_aapl()) == ["2024-01-24"]
    engine = _engine({"AAPL.BA": _cedear([{"effective_date": "2020-01-01", "ratio": "10"}])})
    with pytest.raises(ValueError, match="unexplained CEDEAR price discontinuities on 2024-01-24"):
        engine.prepare_market_data({"AAPL.BA": _aapl()})


def test_already_adjusted_series_is_left_untouched():
    adjusted, _ = reconcile_ratio_changes(_aapl(), "AAPL.BA", RATIO_10_TO_20)
    again, events = reconcile_ratio_changes(adjusted, "AAPL.BA", RATIO_10_TO_20)
    assert events[0]["source_basis"] == "adjusted"
    assert not events[0]["rescaled_prior_bars"]
    pd.testing.assert_frame_equal(again, adjusted)


def test_declaration_contradicted_by_prices_fails_closed():
    history = [RATIO_10_TO_20[0], {**RATIO_10_TO_20[1], "price_series": "adjusted"}]
    with pytest.raises(ValueError, match="not provable from prices"):
        reconcile_ratio_changes(_aapl(), "AAPL.BA", history)


def test_small_ratio_change_cannot_be_told_from_market_noise():
    days = [d.isoformat() for d in AAPL_SESSIONS[:6]]
    frame = _flat(days)
    history = [{"effective_date": "2020-01-01", "ratio": "10"},
               {"effective_date": days[3], "ratio": "11"}]
    with pytest.raises(ValueError, match="not provable"):
        reconcile_ratio_changes(frame, "X.BA", history)
    # A declaration cannot settle it either: the bars support both readings.
    history[1]["price_series"] = "adjusted"
    with pytest.raises(ValueError, match="not provable"):
        reconcile_ratio_changes(frame, "X.BA", history)


def test_ratio_change_without_trades_on_both_sides_fails_closed():
    days = [d.isoformat() for d in AAPL_SESSIONS[:6]]
    frame = _flat(days)
    frame.loc[frame.index < pd.Timestamp(days[3]), "volume"] = 0.0
    history = [{"effective_date": "2020-01-01", "ratio": "10"},
               {"effective_date": days[3], "ratio": "20"}]
    with pytest.raises(ValueError, match="no traded bar on both sides"):
        reconcile_ratio_changes(frame, "X.BA", history)


def test_ratio_change_outside_the_window_is_ignored():
    frame, events = reconcile_ratio_changes(
        _aapl(), "AAPL.BA",
        [*RATIO_10_TO_20, {"effective_date": "2025-06-02", "ratio": "40"}],
    )
    assert [e["effective_date"] for e in events] == ["2024-01-24"]


def test_invalid_price_series_declaration_rejected():
    history = [RATIO_10_TO_20[0], {**RATIO_10_TO_20[1], "price_series": "raw"}]
    with pytest.raises(ValueError, match="price_series"):
        _engine({"AAPL.BA": _cedear(history)})


# ── shares: Yahoo split adjustment and unadjusted splits ──

def test_split_adjusted_share_is_unchanged_and_real_crash_is_allowed():
    days = [d.isoformat() for d in AAPL_SESSIONS[:6]]
    # GGAL.BA 2019-08-12 fell 47% on the PASO result: a real move.
    crash = _frame([(d, p, p, 1000) for d, p in zip(days, [174.55] * 3 + [92.4] * 3)])
    engine = _engine({"GGAL.BA": SHARE})
    prepared = engine.prepare_market_data({"GGAL.BA": crash})
    pd.testing.assert_frame_equal(prepared["GGAL.BA"], crash)


def test_unadjusted_share_split_fails_closed():
    days = [d.isoformat() for d in AAPL_SESSIONS[:6]]
    split = _frame([(d, p, p, 1000) for d, p in zip(days, [8000.0] * 3 + [800.0] * 3)])
    with pytest.raises(ValueError, match="unadjusted split"):
        _engine({"YPFD.BA": SHARE}).prepare_market_data({"YPFD.BA": split})


# ── currency, placeholder bars, executability ──

def test_declared_currency_must_match_metadata():
    days = [d.isoformat() for d in AAPL_SESSIONS[:3]]
    engine = _engine({"YPFD.BA": SHARE})
    with pytest.raises(ValueError, match="no quote currency"):
        engine.prepare_market_data({"YPFD.BA": _flat(days, currency=None)})
    usd = {"AAPLD.BA": {**_cedear(), "currency": "USD"}}
    with pytest.raises(ValueError, match="says USD but the source declared ARS"):
        _engine(usd).prepare_market_data({"AAPLD.BA": _flat(days, currency="ARS")})


def test_volume_column_is_required():
    days = [d.isoformat() for d in AAPL_SESSIONS[:3]]
    frame = _flat(days).drop(columns="volume")
    with pytest.raises(ValueError, match="volume"):
        _engine({"YPFD.BA": SHARE}).prepare_market_data({"YPFD.BA": frame})


def test_holiday_placeholder_bars_dropped_but_traded_ones_fail():
    days = ["2024-01-15", "2024-01-16", "2024-01-17"]
    sessions = (date(2024, 1, 15), date(2024, 1, 17), date(2024, 1, 18))
    placeholder = _frame([(days[0], 10, 10, 5), (days[1], 10, 10, 0), (days[2], 10, 10, 5)])
    kept, dropped = drop_non_session_bars(placeholder, "YPFD.BA", sessions)
    assert dropped == 1 and list(kept.index.date) == [date(2024, 1, 15), date(2024, 1, 17)]
    assert kept.attrs["quote_currency"] == "ARS"
    traded = _frame([(d, 10, 10, 5) for d in days])
    with pytest.raises(ValueError, match="calendar and data disagree"):
        drop_non_session_bars(traded, "YPFD.BA", sessions)


def test_zero_volume_bar_is_not_executable():
    engine = _engine({"YPFD.BA": SHARE})
    stale = pd.Series({"close": 100.0, "volume": 0.0}, name=pd.Timestamp("2024-01-16"))
    missing = pd.Series({"close": 100.0}, name=pd.Timestamp("2024-01-16"))
    assert not engine.can_execute("YPFD.BA", 1, stale)
    assert not engine.can_execute("YPFD.BA", 1, missing)
    assert engine.can_execute("YPFD.BA", 1, stale.copy().replace(0.0, 5.0))


# ── end to end: no artificial gain or loss from unit changes ──

class _Loader:
    def __init__(self, frames):
        self.frames = frames

    def fetch(self, *args, **kwargs):
        out = {}
        for code, frame in self.frames.items():
            copy = frame.copy()
            copy.attrs = dict(frame.attrs)
            out[code] = copy
        return out


class _HoldFromFirstBar:
    def generate(self, data_map):
        return {code: pd.Series(1.0, index=df.index) for code, df in data_map.items()}


def _run(engine, frames, tmp_path):
    first = min(f.index[0] for f in frames.values())
    last = max(f.index[-1] for f in frames.values())
    return engine.run_backtest(
        {"codes": list(frames), "start_date": str(first.date()), "end_date": str(last.date()),
         "initial_cash": 100_000, "source": "local"},
        _Loader(frames), _HoldFromFirstBar(), tmp_path,
    )


def test_holding_cedear_through_real_ratio_change_books_no_artificial_loss(tmp_path):
    """Value follows the ARS price, not the -49% unit change."""
    engine = _engine({"AAPL.BA": _cedear()})
    metrics = _run(engine, {"AAPL.BA": _aapl()}, tmp_path)
    (buy, sell) = engine.fill_records
    # Same quantity on both sides: the engine holds post-change units throughout.
    assert buy.signed_quantity == -sell.signed_quantity
    # Entry on the 2024-01-16 open (21670 old units = 10835 new units).
    assert buy.execution_price == pytest.approx(10835.0)
    # 9 units, 10835 -> 12002.5, no fees: +10.5%. Unadjusted, the same trade
    # would have read 9 x (12002.5 - 21670) = -45% of capital.
    assert sell.execution_price == pytest.approx(12002.5)
    assert metrics["total_return"] == pytest.approx(9 * (12002.5 - 10835.0) / 100_000)
    assert metrics["ar_cedear_ratio_adjustments"][0]["source_basis"] == "unadjusted"
    assert metrics["ar_price_basis"] == "source_split_dividend_adjusted_ohlc_raw_volume"


def test_undeclared_ratio_change_aborts_the_run(tmp_path):
    engine = _engine({"AAPL.BA": _cedear([{"effective_date": "2020-01-01", "ratio": "10"}])})
    with pytest.raises(ValueError, match="unexplained CEDEAR"):
        _run(engine, {"AAPL.BA": _aapl()}, tmp_path)


def test_holding_share_through_split_adjusted_series_is_flat(tmp_path):
    """Yahoo's split-adjusted share series (e.g. YPFD.BA 10:1 on 2026-08-03)
    is continuous; holding through it must neither gain nor lose."""
    days = [d.isoformat() for d in AAPL_SESSIONS[:8]]
    engine = _engine({"YPFD.BA": SHARE})
    metrics = _run(engine, {"YPFD.BA": _flat(days, price=804.0)}, tmp_path)
    assert metrics["total_return"] == pytest.approx(0.0, abs=1e-12)
    assert metrics["ar_cedear_ratio_adjustments"] == []
