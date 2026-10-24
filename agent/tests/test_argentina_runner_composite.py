"""BYMA routing through runner and CompositeEngine on one state/rules contract.

Deterministic: synthetic bars served through the runner's loader seam, a real
``signal_engine.py`` and a JSON ``config.json``; no network, PPI or private
service. The CEDEAR event dates and ratios are Banco Comafi's 2024-01-08
notice (AAPL 10:1 -> 20:1, ex 2024-01-24, credit 2024-01-26).
"""

from __future__ import annotations

import io
import json
import textwrap
from contextlib import redirect_stdout
from datetime import date

import pandas as pd
import pytest

from backtest import runner
from backtest.engines.argentina_equity import ArgentinaEquityEngine
from backtest.engines.composite import CompositeEngine

SESSIONS = [date(2024, 1, d) for d in (15, 16, 17, 18, 19, 22, 23, 24, 25, 26, 29, 30, 31)]
DAYS = SESSIONS[:11]  # 01-15 .. 01-29

INSTRUMENTS = {
    "YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"},
    "GGAL.BA": {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"},
    "AAPL.BA": {
        "kind": "cedear", "currency": "ARS", "lot_size": 1, "tick_size": "0.5",
        "underlying": "AAPL",
        "ratio_history": [
            {"effective_date": "2010-06-24", "ratio": "10"},
            {"effective_date": "2024-01-24", "ratio": "20", "credit_date": "2024-01-26"},
        ],
    },
}


def _config(codes, mode="hold"):
    return {
        "codes": codes,
        "start_date": str(DAYS[0]),
        "end_date": str(DAYS[-1]),
        "source": "yahoo",
        "initial_cash": 1_000_000,
        "position_adjustment": mode,
        "ar_instruments": {c: INSTRUMENTS[c] for c in codes},
        "ar_settlement_sessions": [d.isoformat() for d in SESSIONS],
        "ar_brokerage_rate": 0.005,
        "ar_exchange_fee_rate": 0.0005,
        "ar_fixed_fee": 10,
        "ar_slippage": 0.001,
    }


def _frame(closes, volume=10_000.0):
    index = pd.DatetimeIndex([pd.Timestamp(d) for d in DAYS])
    frame = pd.DataFrame(
        {"open": closes, "high": [c * 1.01 for c in closes],
         "low": [c * 0.99 for c in closes], "close": closes,
         "volume": [volume] * len(closes)},
        index=index,
    )
    frame.attrs["quote_currency"] = "ARS"
    return frame


# YPFD and GGAL drift; AAPL.BA is an unadjusted Yahoo-like series that halves
# on the 2024-01-24 ex-date (25000 -> ~12500) while the underlying is flat.
FRAMES = {
    "YPFD.BA": _frame([20000, 20200, 20400, 20100, 20600, 20800, 21000, 21500, 21300, 21700, 22000]),
    "GGAL.BA": _frame([2000, 2010, 2030, 2050, 2040, 2100, 2120, 2140, 2160, 2150, 2200]),
    "AAPL.BA": _frame([24000, 24200, 24400, 24600, 24800, 25000, 25100, 12560, 12600, 12650, 12700]),
}

SIGNAL_SOURCE = textwrap.dedent('''
    import pandas as pd


    class SignalEngine:
        """Rotate YPFD -> GGAL on day 4 and hold AAPL throughout."""

        seen_min_aapl_close = None

        def generate(self, data_map):
            out = {}
            for code, df in data_map.items():
                n = len(df)
                if code == "YPFD.BA":
                    w = [0.4 if i < 4 else 0.0 for i in range(n)]
                elif code == "GGAL.BA":
                    w = [0.0 if i < 4 else 0.4 for i in range(n)]
                else:
                    SignalEngine.seen_min_aapl_close = float(df["close"].min())
                    w = [0.4] * n
                out[code] = pd.Series(w, index=df.index)
            return out
''')


class _Loader:
    name = "yahoo"

    def __init__(self, frames):
        self.frames = frames

    def fetch(self, codes, start_date, end_date, **kwargs):
        out = {}
        for code in codes:
            out[code] = self.frames[code].copy()
            out[code].attrs = dict(self.frames[code].attrs)
        return out


class _Signal:
    def generate(self, data_map):
        namespace: dict = {}
        exec(compile(SIGNAL_SOURCE, "signal_engine.py", "exec"), namespace)  # noqa: S102 - test fixture source
        return namespace["SignalEngine"]().generate(data_map)


def _fills(engine):
    return [(f.symbol, f.action, f.timestamp.date(), round(f.signed_quantity, 9),
             round(f.execution_price, 9), round(f.fee, 9)) for f in engine.fill_records]


def _run(engine, codes, tmp_path, mode="hold"):
    with redirect_stdout(io.StringIO()):
        metrics = engine.run_backtest(_config(codes, mode), _Loader(FRAMES), _Signal(), tmp_path)
    return metrics


CODES = ["YPFD.BA", "GGAL.BA", "AAPL.BA"]


@pytest.mark.parametrize("mode", ["hold", "rebalance"])
def test_standalone_and_composite_share_one_state_contract(tmp_path, mode):
    standalone = runner._create_market_engine("yahoo", _config(CODES, mode), CODES)
    assert isinstance(standalone, ArgentinaEquityEngine)
    composite = CompositeEngine(_config(CODES, mode), CODES)
    a = _run(standalone, CODES, tmp_path / "a", mode)
    b = _run(composite, CODES, tmp_path / "b", mode)
    assert _fills(standalone) == _fills(composite)
    assert [s.equity for s in standalone.equity_snapshots] == pytest.approx(
        [s.equity for s in composite.equity_snapshots])
    assert a["total_return"] == pytest.approx(b["total_return"])
    assert b["market_diagnostics"]["ar_equity"]["ar_cedear_ratio_adjustments"][0][
        "credit_date"] == "2024-01-26"
    # Rotation happened and capital never went negative in either path.
    actions = {(sym, act) for sym, act, *_ in _fills(standalone)}
    assert ("YPFD.BA", "close") in actions and ("GGAL.BA", "open") in actions
    assert min(s.capital for s in composite.equity_snapshots) >= -1e-6


def test_prepare_runs_before_signals_so_strategies_see_adjusted_prices(tmp_path):
    namespace: dict = {}
    exec(compile(SIGNAL_SOURCE, "signal_engine.py", "exec"), namespace)  # noqa: S102
    signal_cls = namespace["SignalEngine"]

    class Recording:
        def generate(self, data_map):
            return signal_cls().generate(data_map)

    engine = runner._create_market_engine("yahoo", _config(["AAPL.BA"]), ["AAPL.BA"])
    with redirect_stdout(io.StringIO()):
        engine.run_backtest(_config(["AAPL.BA"]), _Loader(FRAMES), Recording(), tmp_path)
    # Pre-ex bars restated by 10/20: no "crash" from ~25000 to ~12500 is visible.
    assert signal_cls.seen_min_aapl_close == pytest.approx(12000.0)


def test_cedear_quantities_are_whole_real_certificates_before_the_ex_date(tmp_path):
    engine = runner._create_market_engine("yahoo", _config(CODES), CODES)
    _run(engine, CODES, tmp_path)
    buy = next(f for f in engine.fill_records if f.symbol == "AAPL.BA" and f.action == "open")
    assert buy.timestamp.date() < date(2024, 1, 24)
    assert buy.signed_quantity % 2 == 0  # 2 adjusted units per real certificate
    # Tick 0.5 on real prices is 0.25 on restated prices.
    assert (buy.execution_price / 0.25) == pytest.approx(round(buy.execution_price / 0.25))


def test_fee_and_cash_identity_over_the_full_run(tmp_path):
    engine = runner._create_market_engine("yahoo", _config(CODES), CODES)
    _run(engine, CODES, tmp_path)
    cash = 1_000_000.0
    for f in engine.fill_records:
        notional = abs(f.signed_quantity) * f.execution_price
        assert f.fee == pytest.approx(notional * 0.0055 + 10)
        cash += (-notional if f.signed_quantity > 0 else notional) - f.fee
    assert engine.capital == pytest.approx(cash)
    assert engine.equity_snapshots[-1].equity == pytest.approx(
        cash + sum(p["estimated_net_proceeds"] for p in engine.unliquidated_positions))


def test_runner_main_end_to_end_with_json_config(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(_config(CODES)), encoding="utf-8")
    (run_dir / "code" / "signal_engine.py").write_text(SIGNAL_SOURCE, encoding="utf-8")
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    monkeypatch.delenv("VIBE_TRADING_DATA_CACHE", raising=False)
    monkeypatch.setattr(runner, "_get_loader", lambda source: (lambda: _Loader(FRAMES)))
    out = io.StringIO()
    with redirect_stdout(out):
        runner.main(run_dir)
    printed = out.getvalue()
    metrics = json.loads(printed[printed.index("{"):])
    assert metrics["trade_count"] >= 2
    assert metrics["ar_price_basis"] == "source_split_dividend_adjusted_ohlc_raw_volume"
    # The same run outside the CLI gives the same headline result.
    engine = runner._create_market_engine("yahoo", _config(CODES), CODES)
    direct = _run(engine, CODES, tmp_path / "direct")
    assert metrics["total_return"] == pytest.approx(direct["total_return"])
    assert (run_dir / "artifacts").is_dir()


# ── unsupported cases stay closed ──

def test_ba_codes_cannot_share_a_composite_pool_with_other_markets():
    with pytest.raises(ValueError, match="cannot share a composite pool"):
        runner._create_market_engine("yahoo", _config(["GGAL.BA"]) | {}, ["GGAL.BA", "AAPL.US"])


def test_mixed_ars_usd_byma_lines_rejected():
    config = _config(["GGAL.BA"])
    config["ar_instruments"]["GGALD.BA"] = {
        "kind": "share", "currency": "USD", "lot_size": 1, "tick_size": "0.01"}
    with pytest.raises(ValueError, match="mixed ARS/USD"):
        runner._create_market_engine("yahoo", config, ["GGAL.BA", "GGALD.BA"])


def test_calendar_not_covering_the_run_fails_closed(tmp_path):
    config = _config(["GGAL.BA"])
    config["ar_settlement_sessions"] = [d.isoformat() for d in DAYS]  # no tail
    engine = runner._create_market_engine("yahoo", config, ["GGAL.BA"])
    with pytest.raises(ValueError, match="does not cover"):
        with redirect_stdout(io.StringIO()):
            engine.run_backtest(config, _Loader(FRAMES), _Signal(), tmp_path)


def test_non_iso_session_strings_rejected():
    config = _config(["GGAL.BA"])
    config["ar_settlement_sessions"] = ["2024/01/15", "2024-01-16"]
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        runner._create_market_engine("yahoo", config, ["GGAL.BA"])
