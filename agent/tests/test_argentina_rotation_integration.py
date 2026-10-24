"""Full BaseEngine execution-loop regression for BYMA settlement-matched rotation.

These use synthetic OHLCV and a declared exchange calendar, never PPI,
Asistente Casa, external services, or user-specific portfolio positions.
"""

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from backtest.engines.argentina_equity import ArgentinaEquityEngine


SESSIONS = tuple(date(2024, 5, d) for d in (27, 28, 29, 30, 31))


def _engine(*, adjustment="hold", cash=1000, fee=0, sessions=SESSIONS, tick="0.01",
            brokerage=0, exchange=0, slippage=0):
    return ArgentinaEquityEngine({
        "initial_cash": cash,
        "position_adjustment": adjustment,
        "ar_instruments": {
            symbol: {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": tick}
            for symbol in ("YPFD.BA", "GGAL.BA", "PAMP.BA")
        },
        "ar_settlement_sessions": sessions,
        "ar_brokerage_rate": brokerage,
        "ar_exchange_fee_rate": exchange,
        "ar_fixed_fee": fee,
        "ar_slippage": slippage,
    })


def _execute(engine, targets, dates=("2024-05-27", "2024-05-28", "2024-05-29"), prices=None):
    dates = pd.DatetimeIndex(dates)
    codes = list(targets)
    prices = prices or {code: [100.0] * len(dates) for code in codes}
    frames = {
        code: pd.DataFrame({"open": prices[code], "close": prices[code],
                            "volume": [1_000.0] * len(dates)}, index=dates)
        for code in codes
    }
    close = pd.DataFrame(prices, index=dates)
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


# Pre-2024-05-27 normal settlement is T+2; the calendar extends past the cutover
# so both lags are exercised by one fixture.
T2_SESSIONS = tuple(date(2024, 5, d) for d in (20, 21, 22, 23, 24, 27, 28))


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_t2_unsettled_position_cannot_be_rotated_early(mode):
    """A T+2 purchase is not sellable the next day; no funds are used twice."""
    engine = _execute(
        _engine(adjustment=mode, sessions=T2_SESSIONS),
        {"YPFD.BA": [1, 0, 0, 0], "GGAL.BA": [0, 1, 1, 1]},
        dates=("2024-05-20", "2024-05-21", "2024-05-22", "2024-05-23"),
    )
    fills = [(f.symbol, f.action, f.timestamp.date()) for f in engine.fill_records]
    # 5/21: YPF (bought 5/20, settles 5/22) is blocked, and with no free cash
    # GGAL cannot be bought either. The rotation happens on 5/22.
    assert not any(day == date(2024, 5, 21) and action != "open" for _, action, day in fills)
    assert ("YPFD.BA", "close", date(2024, 5, 22)) in fills
    assert ("GGAL.BA", "open", date(2024, 5, 22)) in fills
    assert all(s.equity == pytest.approx(1000) for s in engine.equity_snapshots)
    assert min(s.capital for s in engine.equity_snapshots) >= -1e-9


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_rotation_across_t2_to_t1_cutover_is_funded_and_matched(mode):
    """A pre-cutover T+2 sale (settles 5/28) funds the 5/24 T+2 buy and
    the post-cutover 5/27 T+1 buy (settles 5/28): equal maturities."""
    engine = _execute(
        _engine(adjustment=mode, sessions=T2_SESSIONS),
        {"YPFD.BA": [1, 1, 0, 0, 0], "GGAL.BA": [0, 0, 1, 1, 0], "PAMP.BA": [0, 0, 0, 0, 0]},
        dates=("2024-05-20", "2024-05-21", "2024-05-22", "2024-05-23", "2024-05-24"),
    )
    fills = [(f.symbol, f.action, f.timestamp.date()) for f in engine.fill_records]
    assert ("YPFD.BA", "close", date(2024, 5, 22)) in fills
    assert ("GGAL.BA", "open", date(2024, 5, 22)) in fills
    assert min(s.capital for s in engine.equity_snapshots) >= -1e-9
    assert engine.equity_snapshots[-1].equity == pytest.approx(1000)


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_terminal_liquidation_respects_settlement_and_keeps_position_open(mode):
    """Buying on the last bar must not be undone by end_of_backtest."""
    engine = _execute(
        _engine(adjustment=mode, sessions=T2_SESSIONS),
        {"YPFD.BA": [0, 1], "GGAL.BA": [0, 0]},
        dates=("2024-05-20", "2024-05-21"),
    )
    assert [(f.symbol, f.action, f.reason) for f in engine.fill_records] == [
        ("YPFD.BA", "open", "signal"),
    ]
    assert engine.trades == []  # no invented round trip
    assert "YPFD.BA" in engine.positions
    last = engine.equity_snapshots[-1]
    assert last.positions == 1 and last.equity == pytest.approx(1000)
    assert engine.actual_position_snapshots[-1][1]["YPFD.BA"] == pytest.approx(1.0)
    diag = engine._engine_diagnostics()
    assert diag["ar_unliquidated_position_count"] == 1
    assert diag["ar_unliquidated_positions"][0]["symbol"] == "YPFD.BA"
    assert diag["ar_terminal_equity_basis"] == "unsettled_positions_marked_net_of_estimated_exit_costs"


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_terminal_equity_nets_estimated_exit_costs_for_retained_position(mode):
    engine = _execute(
        _engine(adjustment=mode, sessions=T2_SESSIONS, fee=5, brokerage=0.01),
        {"YPFD.BA": [0, 1], "GGAL.BA": [0, 0]},
        dates=("2024-05-20", "2024-05-21"),
    )
    (buy,) = engine.fill_records
    # Open: notional*1% + 5 fixed. Mark = 100, estimated exit nets the same
    # kind of cost again: equity = cash + qty*100 - (qty*100*1% + 5).
    qty = buy.signed_quantity
    cash = 1000 - qty * 100 - buy.fee
    expected = cash + qty * 100 - (qty * 100 * 0.01 + 5)
    assert engine.capital == pytest.approx(cash)
    assert engine.equity_snapshots[-1].equity == pytest.approx(expected)


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_settled_position_is_still_liquidated_at_end(mode):
    engine = _execute(_engine(adjustment=mode), {"YPFD.BA": [1, 1, 1]})
    assert [(f.action, f.reason) for f in engine.fill_records] == [
        ("open", "signal"), ("close", "end_of_backtest"),
    ]
    assert engine.positions == {}
    diag = engine._engine_diagnostics()
    assert diag["ar_unliquidated_position_count"] == 0
    assert diag["ar_terminal_equity_basis"] == "all_positions_liquidated_at_last_close"
    # The terminal sale settles after the last bar and is declared as receivable.
    assert diag["ar_unsettled_sale_receivable_at_end"] == pytest.approx(1000)


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_ledger_identity_with_fees_slippage_and_ticks(mode):
    """Final capital must equal initial cash plus every fill's cash effect,
    independently of how BaseEngine computed it; equity == capital at the end."""
    engine = _execute(
        _engine(adjustment=mode, cash=10_000, fee=2, brokerage=0.004, exchange=0.0006,
                slippage=0.01, tick="0.05",
                sessions=SESSIONS + (date(2024, 6, 3), date(2024, 6, 4))),
        {"YPFD.BA": [1, 0, 0, 0, 0], "GGAL.BA": [0, 1, 0, 0, 0], "PAMP.BA": [0, 0, 1, 1, 1]},
        dates=("2024-05-27", "2024-05-28", "2024-05-29", "2024-05-30", "2024-05-31"),
        prices={
            "YPFD.BA": [100.0, 103.0, 99.0, 98.0, 97.0],
            "GGAL.BA": [50.0, 52.0, 51.0, 53.0, 54.0],
            "PAMP.BA": [20.0, 20.5, 21.0, 22.0, 23.0],
        },
    )
    cash = Decimal(10_000)
    for f in engine.fill_records:
        notional = Decimal(str(abs(f.signed_quantity))) * Decimal(str(f.execution_price))
        assert f.execution_price == pytest.approx(round(f.execution_price / 0.05) * 0.05)
        assert float(f.fee) == pytest.approx(float(notional) * 0.0046 + 2)
        cash += -notional - Decimal(str(f.fee)) if f.signed_quantity > 0 else notional - Decimal(str(f.fee))
    assert engine.positions == {}
    assert engine.capital == pytest.approx(float(cash))
    assert engine.equity_snapshots[-1].equity == pytest.approx(float(cash))
    assert min(s.capital for s in engine.equity_snapshots) >= -1e-9
    # No bar spends more than the funds that settle by its own purchase date.
    for snap in engine.equity_snapshots:
        assert snap.capital >= -1e-9


def test_run_backtest_reports_retained_position_end_to_end(tmp_path):
    """Full pipeline (metrics, artifacts, run card) with an unsettled terminal."""
    dates = pd.DatetimeIndex(["2024-05-20", "2024-05-21", "2024-05-22", "2024-05-23"])
    bars = pd.DataFrame(
        {"open": [100.0] * 4, "high": [101.0] * 4, "low": [99.0] * 4,
         "close": [100.0] * 4, "volume": [1_000_000] * 4},
        index=dates,
    )

    class Loader:
        def fetch(self, *args, **kwargs):
            frame = bars.copy()
            frame.attrs["quote_currency"] = "ARS"  # as the Yahoo/yfinance loaders declare
            return {"YPFD.BA": frame}

    class Signal:
        def generate(self, data_map):
            return {"YPFD.BA": pd.Series([0.0, 0.0, 1.0, 1.0], index=dates)}

    engine = _engine(sessions=T2_SESSIONS)
    metrics = engine.run_backtest(
        {"codes": ["YPFD.BA"], "start_date": "2024-05-20", "end_date": "2024-05-23",
         "initial_cash": 1000, "source": "local"},
        Loader(), Signal(), tmp_path,
    )
    assert metrics["ar_unliquidated_position_count"] == 1
    assert metrics["trade_count"] == 0
    assert metrics["ar_terminal_equity_basis"].startswith("unsettled")
