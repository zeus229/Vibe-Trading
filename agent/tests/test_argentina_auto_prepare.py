"""Automatic BYMA backtest preparation: one-shot YPFD, provenance, fail-closed.

Replays the first production attempt (2026-10-24): a conversational agent
asked for a YPFD.BA buy-and-hold, 2024-06-01..2024-09-30, 1,000,000 ARS, 95%.
It failed six times on `engine`, `ar_instruments`, `kind`, `lot_size` and
`tick_size`. Here the minimal config it naturally writes must run first time.
Synthetic bars; the Yahoo descriptor is stubbed; no network.
"""

from __future__ import annotations

import io
import json
import textwrap
from contextlib import redirect_stdout
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from backtest import runner
from backtest.engines.argentina_equity import ArgentinaEquityEngine
from backtest.engines.argentina_metadata import (
    classify_short_name,
    equity_tick,
    observed_sessions,
)

# Argentine market holidays in the window (BYMA closed): 2024-06-17 (Güemes,
# moved), 2024-06-20 (Belgrano), 2024-06-21 (bridge), 2024-07-09
# (Independence). Yahoo emits flat zero-volume placeholder bars on some.
HOLIDAYS = {date(2024, 6, 17), date(2024, 6, 20), date(2024, 6, 21), date(2024, 7, 9)}
PLACEHOLDERS = {date(2024, 6, 20), date(2024, 7, 9)}
DAYS = [d.date() for d in pd.bdate_range("2024-06-03", "2024-09-30")]

MINIMAL_CONFIG = {
    "source": "yahoo", "codes": ["YPFD.BA"], "start_date": "2024-06-01",
    "end_date": "2024-09-30", "interval": "1D", "initial_cash": 1_000_000,
    "commission": 0.001, "engine": "daily", "position_adjustment": "hold",
}
SIGNAL = textwrap.dedent('''
    import pandas as pd


    class SignalEngine:
        def generate(self, data_map):
            return {c: pd.Series(0.95, index=d.index) for c, d in data_map.items()}
''')


def _ypfd():
    rows = []
    price = 26000.0
    for day in DAYS:
        if day in HOLIDAYS and day not in PLACEHOLDERS:
            continue
        price *= 1.002
        volume = 0.0 if day in PLACEHOLDERS else 500_000.0
        rows.append((pd.Timestamp(day), price, volume))
    frame = pd.DataFrame(
        {"open": [r[1] for r in rows], "high": [r[1] * 1.01 for r in rows],
         "low": [r[1] * 0.99 for r in rows], "close": [r[1] for r in rows],
         "volume": [r[2] for r in rows]},
        index=pd.DatetimeIndex([r[0] for r in rows]),
    )
    frame.attrs["quote_currency"] = "ARS"
    return frame


class _Loader:
    name = "yahoo"

    def fetch(self, codes, start_date, end_date, **kwargs):
        out = {}
        for code in codes:
            frame = _ypfd()
            out[code] = frame
        return out


@pytest.fixture
def yahoo_says_share(monkeypatch):
    monkeypatch.setattr(ArgentinaEquityEngine, "describe_instrument",
                        staticmethod(lambda symbol: "YPF SOCIEDAD ANONIMA"))


def _run_main(tmp_path, monkeypatch, config):
    run_dir = tmp_path / "run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (run_dir / "code" / "signal_engine.py").write_text(SIGNAL, encoding="utf-8")
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    monkeypatch.delenv("VIBE_TRADING_DATA_CACHE", raising=False)
    monkeypatch.setattr(runner, "_get_loader", lambda source: _Loader)
    out = io.StringIO()
    with redirect_stdout(out):
        runner.main(run_dir)
    text = out.getvalue()
    return json.loads(text[text.index("{"):]), run_dir


# ── the replayed conversational request ──

def test_minimal_ypfd_config_runs_first_time(tmp_path, monkeypatch, yahoo_says_share):
    printed, run_dir = _run_main(tmp_path, monkeypatch, MINIMAL_CONFIG)
    assert printed["trade_count"] == 1
    # The one-line basis is a scalar, so it reaches the agent's tool result.
    basis = printed["ar_data_basis"]
    for label in ("kind=share[third_party]", "currency=ARS[derived]", "lot_size=1[assumption]",
                  "tick_size=CIR3569 bands[official]", "[derived; settlement tail weekdays assumed]",
                  "brokerage 0.1000% from commission"):
        assert label in basis
    metrics = json.loads((run_dir / "artifacts" / "byma_preparation.json").read_text())
    prov = metrics["ar_metadata_provenance"]["YPFD.BA"]
    assert prov["kind"]["kind"] == "third_party"
    assert prov["currency"]["kind"] == "derived"
    assert prov["lot_size"]["kind"] == "assumption"
    assert prov["tick_size"]["kind"] == "official"
    assert metrics["ar_calendar_provenance"]["kind"] == "derived"
    assert metrics["ar_fee_basis"]["brokerage_source"] == "commission"
    assert metrics["ar_fee_basis"]["brokerage_rate"] == pytest.approx(0.001)
    assert metrics["ar_tick_schedule"]["source"] == "byma_cir3569_anexo_v20240704"
    fills = [json.loads(line) for line in
             (run_dir / "artifacts" / "fills.jsonl").read_text().splitlines()]
    buy = fills[0]
    # 0.1% brokerage from `commission`, nothing silently zeroed.
    notional = abs(buy["signed_quantity"]) * buy["execution_price"]
    assert buy["fee"] == pytest.approx(notional * 0.001)
    # Bought before 2024-07-04: the official band is applied and flagged.
    assert metrics["ar_tick_schedule"]["fills_before_official_version"] >= 1


def test_the_failed_sessions_final_config_now_runs(tmp_path, monkeypatch, yahoo_says_share):
    """The exact config the agent ended with (PPI-style extra keys, no tick)."""
    config = {**MINIMAL_CONFIG, "source": "yfinance",
              "ar_instruments": {"YPFD.BA": {"instrument_type": "ACCIONES", "currency": "ARS",
                                             "market": "BYMA", "kind": "share", "lot_size": 1}},
              "strategy_params": {"target_allocation": 0.95}}
    printed, run_dir = _run_main(tmp_path, monkeypatch, config)
    assert printed["trade_count"] == 1
    assert "kind=share[declared]" in printed["ar_data_basis"]
    prep = json.loads((run_dir / "artifacts" / "byma_preparation.json").read_text())
    assert prep["ar_metadata_provenance"]["YPFD.BA"]["kind"] == {"kind": "declared"}


def test_engine_field_misuse_names_the_fix(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps({**MINIMAL_CONFIG, "engine": "argentina_equity"}))
    (run_dir / "code" / "signal_engine.py").write_text(SIGNAL)
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit):
        runner.main(run_dir)
    assert "selected from `codes`, keep 'daily'" in out.getvalue()


# ── provenance and derivation units ──

@pytest.mark.parametrize("name,kind", [
    ("YPF SOCIEDAD ANONIMA", "share"),
    ("GRUPO FINANCIERO GALICIA SA", "share"),
    ("APPLE INC CEDEAR(REPR 1/20 SHR)", "cedear"),
    ("COCA-COLA CO CEDEARS EACH 5  RE", "cedear"),
    ("STATE ST SPDR S&P 500 ETF TR CE", None),   # truncated at 31 chars
    ("SOME LONG ISSUER NAME THAT IS CUT", None),  # at/over the limit
    ("", None),
    (None, None),
])
def test_yahoo_descriptor_classification(name, kind):
    assert classify_short_name(name)[0] == kind


@pytest.mark.parametrize("price,tick", [
    ("1", "0.001"), ("1.01", "0.005"), ("10", "0.01"), ("100", "0.10"),
    ("250", "0.25"), ("1000", "1.00"), ("5000", "5.00"), ("10000", "10.00"),
    ("26000", "25.00"), ("50000", "25.00"), ("50000.01", "50.00"),
])
def test_cir3569_equity_tick_bands(price, tick):
    assert equity_tick(Decimal(price)) == Decimal(tick)


def test_observed_sessions_exclude_placeholders_and_add_a_labelled_tail():
    sessions, provenance = observed_sessions([_ypfd()])
    assert not HOLIDAYS & set(sessions)
    assert sessions[-11] == date(2024, 9, 30)
    assert sessions[-10:] == tuple(d.date() for d in pd.bdate_range("2024-10-01", periods=10))
    assert provenance["kind"] == "derived" and "holidays unknown" in provenance["tail_assumption"]


def test_holiday_placeholders_are_dropped_not_traded(yahoo_says_share):
    engine = ArgentinaEquityEngine({})
    prepared = engine.prepare_market_data({"YPFD.BA": _ypfd()})
    assert not PLACEHOLDERS & {ts.date() for ts in prepared["YPFD.BA"].index}


def test_no_fee_configuration_is_reported_not_hidden():
    basis = ArgentinaEquityEngine({}).fee_basis
    assert basis["brokerage_source"] == "none_configured" and basis["kind"] == "assumption"


def test_declared_metadata_wins_and_skips_the_vendor(monkeypatch):
    def boom(symbol):
        raise AssertionError("descriptor must not be consulted")

    monkeypatch.setattr(ArgentinaEquityEngine, "describe_instrument", staticmethod(boom))
    engine = ArgentinaEquityEngine({"ar_instruments": {
        "YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"}}})
    engine.prepare_market_data({"YPFD.BA": _ypfd()})
    assert engine.metadata_provenance["YPFD.BA"]["tick_size"] == {"kind": "declared"}


# ── fail closed, with every gap reported at once ──

def test_cedear_is_not_completed_automatically(monkeypatch):
    monkeypatch.setattr(ArgentinaEquityEngine, "describe_instrument",
                        staticmethod(lambda s: "APPLE INC CEDEAR(REPR 1/20 SHR)"))
    frame = _ypfd()
    with pytest.raises(ValueError) as err:
        ArgentinaEquityEngine({}).prepare_market_data({"AAPL.BA": frame})
    message = str(err.value)
    assert "Yahoo identifies a CEDEAR" in message and "ratio_history" in message
    assert "credit_date" in message


def test_all_configuration_problems_reported_together():
    with pytest.raises(ValueError) as err:
        ArgentinaEquityEngine({"ar_instruments": {
            "YPFD.BA": {"classification": "share", "kind": "acciones", "lot_size": 0},
            "AAPL.BA": {"kind": "cedear"},
        }})
    message = str(err.value)
    for expected in ("YPFD.BA.kind must be 'share' or 'cedear'",
                     "YPFD.BA.lot_size must be a positive integer",
                     "AAPL.BA.underlying is required",
                     "AAPL.BA.ratio_history (dated CEDEAR ratios) is required",
                     'Do not set "engine"'):
        assert expected in message


def test_unknown_descriptor_fails_closed_with_the_key_to_set(monkeypatch):
    monkeypatch.setattr(ArgentinaEquityEngine, "describe_instrument",
                        staticmethod(lambda s: "STATE ST SPDR S&P 500 ETF TR CE"))
    with pytest.raises(ValueError, match=r"declare ar_instruments\.SPY\.BA\.kind"):
        ArgentinaEquityEngine({}).prepare_market_data({"SPY.BA": _ypfd()})
