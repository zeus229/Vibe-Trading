"""A BYMA backtest report can cite its capital, equity, fees and monthly path.

Production 2026-10-26 (run 20261026_024335_72_c1e9e1, YPFD.BA): the backtest
ran, but the grounding gate cut six figures from the report:
- capital and final equity, written "1.000.000,00 ARS" / "953.689,78 ARS", were
  checked only against currency-matching evidence, and backtest outputs carried
  no currency, so the exact `call::backtest.initial_cash` ref the gate itself
  proposed failed as "not_in_referenced_call";
- month-end drawdowns and equity cited as `call::rows[16][2]` of a
  read_run_artifact result named a shown table cell the gate could not resolve.

The fix declares the run's settlement currency in the engine's run card and
tags only that run's amount fields with it, and resolves `rows[i][j]` to the
one recorded cell. What must stay closed is pinned here too: invented values,
a different currency, unshown rows and unrecorded columns still fail.

Synthetic bars; the Yahoo descriptor is stubbed; no network.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from src.agent.grounding import GroundingLedger
from src.tools.run_artifact_tool import read_run_artifact
from backtest.engines.argentina_equity import ArgentinaEquityEngine

PROMPT = ("Ejecutá un backtest buy-and-hold de YPFD.BA del 01/06/2024 al 30/09/2024, "
          "capital 1.000.000 ARS, 95%, comisión 0,1%.")
BT = "call_bt|fc_bt"
EQ = "call_eq|fc_eq"
TR = "call_tr|fc_tr"


def _bars() -> pd.DataFrame:
    days = [d for d in pd.bdate_range("2024-06-03", "2024-09-30")
            if d.date().isoformat() not in {"2024-06-17", "2024-06-20", "2024-06-21", "2024-07-09"}]
    prices = [2700.0 + 3.0 * ((i * 37) % 23) - 1.5 * i for i in range(len(days))]
    frame = pd.DataFrame({"open": prices, "high": [p * 1.01 for p in prices],
                          "low": [p * 0.99 for p in prices], "close": prices,
                          "volume": [100_000.0] * len(days)}, index=pd.DatetimeIndex(days))
    frame.attrs["quote_currency"] = "ARS"
    return frame


class _Loader:
    def fetch(self, *args, **kwargs):
        frame = _bars()
        out = frame.copy()
        out.attrs = dict(frame.attrs)
        return {"YPFD.BA": out}


class _Hold:
    def generate(self, data_map):
        return {c: pd.Series(0.95, index=d.index) for c, d in data_map.items()}


@pytest.fixture()
def session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    monkeypatch.setattr(ArgentinaEquityEngine, "describe_instrument",
                        staticmethod(lambda s: "YPF SOCIEDAD ANONIMA"))
    run_dir = tmp_path / "run"
    engine = ArgentinaEquityEngine({"commission": 0.001})
    with redirect_stdout(io.StringIO()):
        metrics = engine.run_backtest(
            {"codes": ["YPFD.BA"], "start_date": "2024-06-01", "end_date": "2024-09-30",
             "initial_cash": 1_000_000, "commission": 0.001, "source": "yahoo"},
            _Loader(), _Hold(), run_dir)
    ledger = GroundingLedger(run_dir=tmp_path, user_message=PROMPT)
    ledger.ingest_tool_result(
        tool_name="backtest", arguments={"run_dir": str(run_dir)},
        result=json.dumps({"status": "ok", "exit_code": 0, "stdout": "", "run_dir": str(run_dir)}),
        call_id=BT, success=True)
    equity = read_run_artifact(str(run_dir), "equity", columns=["timestamp", "equity", "drawdown"])
    ledger.ingest_tool_result(
        tool_name="read_run_artifact",
        arguments={"run_dir": str(run_dir), "artifact": "equity"},
        result=equity, call_id=EQ, success=True)
    trades = read_run_artifact(str(run_dir), "trades")
    ledger.ingest_tool_result(
        tool_name="read_run_artifact", arguments={"run_dir": str(run_dir), "artifact": "trades"},
        result=trades, call_id=TR, success=True)
    rows = json.loads(equity)["rows"]
    return ledger, metrics, rows, engine, run_dir


def _ars(value: float) -> str:
    whole, frac = f"{value:,.2f}".split(".")
    return whole.replace(",", ".") + "," + frac


def _pct(value: float) -> str:
    return f"{value * 100:.4f}".replace(".", ",") + "%"


def _answer(*items: tuple[str, str, str]) -> str:
    prose = "\n".join(f"- {label}: {text}" for text, label, _ in items)
    block = "\n".join(f"{text} | observed | {label} | {ref}" for text, label, ref in items)
    return prose + "\n\n```figures\n" + block + "\n```"


def _reasons(result) -> list:
    return [(i.get("value"), i.get("reason")) for i in result.issues if i.get("value") is not None]


def test_run_card_declares_the_settlement_currency_and_fees(session):
    _, metrics, _, engine, run_dir = session
    card = json.loads((run_dir / "run_card.json").read_text())
    assert card["metrics"]["settlement_currency"] == "ARS"
    fills = engine.fill_records
    assert card["metrics"]["ar_total_fees"] == pytest.approx(sum(f.fee for f in fills))
    assert card["metrics"]["ar_buy_fees"] == pytest.approx(sum(f.fee for f in fills if f.signed_quantity > 0))
    assert card["metrics"]["ar_fill_count"] == len(fills) == 2


def test_the_six_figures_cut_in_production_now_ground(session):
    ledger, metrics, rows, _, _ = session
    month_ends = [i for i, r in enumerate(rows)
                  if i + 1 == len(rows) or rows[i + 1][0][:7] != r[0][:7]]
    june, aug, last = month_ends[0], month_ends[2], len(rows) - 1
    answer = _answer(
        (f"{_ars(1_000_000)} ARS", "capital inicial", f"{BT}::backtest.initial_cash"),
        (f"{_ars(metrics['final_value'])} ARS", "patrimonio final", f"{BT}::metrics.final_value"),
        (_pct(rows[june][2]), "drawdown junio", f"{EQ}::rows[{june}][2]"),
        (_pct(rows[aug][2]), "drawdown agosto", f"{EQ}::rows[{aug}][2]"),
        (f"{_ars(rows[last][1])} ARS", "patrimonio 30/09", f"{EQ}::rows[{last}][1]"),
        (_pct(rows[last][2]), "drawdown 30/09", f"{EQ}::rows[{last}][2]"),
    )
    result = ledger.validate_final_answer(answer)
    assert result.valid, _reasons(result)


def test_fees_trades_and_monthly_equity_are_citable(session):
    ledger, metrics, rows, engine, _ = session
    buy, sell = engine.fill_records
    month_ends = [i for i, r in enumerate(rows)
                  if i + 1 == len(rows) or rows[i + 1][0][:7] != r[0][:7]]
    items = [
        (f"{_ars(metrics['ar_total_fees'])} ARS", "comisiones totales", f"{BT}::metrics.ar_total_fees"),
        (f"{_ars(buy.fee)} ARS", "comisión compra", f"{BT}::metrics.ar_buy_fees"),
        (f"{_ars(sell.fee)} ARS", "comisión venta", f"{BT}::metrics.ar_sell_fees"),
        (f"{int(buy.signed_quantity)}", "acciones compradas", f"{TR}::qty"),
        (_pct(metrics["total_return"]), "rendimiento total", f"{BT}::metrics.total_return"),
    ]
    items += [(f"{_ars(rows[i][1])} ARS", f"patrimonio {rows[i][0]}", f"{EQ}::rows[{i}][1]")
              for i in month_ends]
    result = ledger.validate_final_answer(_answer(*items))
    assert result.valid, _reasons(result)


# ── what must stay closed ──

def test_invented_amount_is_still_rejected(session):
    ledger, metrics, _, _, _ = session
    wrong = _answer((f"{_ars(metrics['final_value'] + 1000)} ARS", "patrimonio final",
                     f"{BT}::metrics.final_value"))
    assert not ledger.validate_final_answer(wrong).valid


def test_right_amount_in_another_currency_is_rejected(session):
    """An explicit USD code never matches the run's ARS amounts. ("US$"/"$"
    carry no code in the figure parser, so they were never currency-checked;
    that pre-existing behaviour is unchanged here.)"""
    ledger, metrics, rows, _, _ = session
    usd = _answer((f"{_ars(metrics['final_value'])} USD", "patrimonio final",
                   f"{BT}::metrics.final_value"))
    assert not ledger.validate_final_answer(usd).valid
    cell = _answer((f"{_ars(rows[1][1])} USD", "patrimonio", f"{EQ}::rows[1][1]"))
    assert not ledger.validate_final_answer(cell).valid


def test_cell_refs_resolve_only_recorded_cells(session):
    ledger, _, rows, _, _ = session
    # Right value, wrong cell: rows[1] holds a different drawdown than rows[last].
    last = len(rows) - 1
    shifted = _answer((_pct(rows[last][2]), "drawdown", f"{EQ}::rows[1][2]"))
    assert rows[1][2] != rows[last][2]
    assert not ledger.validate_final_answer(shifted).valid
    # Beyond the shown rows, and the timestamp column, resolve to nothing.
    beyond = _answer((_pct(rows[last][2]), "drawdown", f"{EQ}::rows[{last + 5}][2]"))
    assert not ledger.validate_final_answer(beyond).valid
    # The timestamp column is never evidence: a real amount aimed at it fails.
    stamp = _answer((f"{_ars(rows[0][1])} ARS", "patrimonio", f"{EQ}::rows[0][0]"))
    assert not ledger.validate_final_answer(stamp).valid


def test_run_without_declared_currency_keeps_currencyless_evidence(tmp_path):
    """Another engine's run card with no settlement_currency: amounts stay untagged."""
    run_dir = tmp_path / "us"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "metrics.csv").write_text("final_value,total_return\n1105000.0,0.105\n")
    (run_dir / "run_card.json").write_text(json.dumps({
        "backtest": {"initial_cash": 1000000}, "metrics": {"final_value": 1105000.0},
        "artifacts": []}))
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Backtest de AAPL")
    ledger.ingest_tool_result(
        tool_name="backtest", arguments={"run_dir": str(run_dir)},
        result=json.dumps({"status": "ok", "exit_code": 0, "run_dir": str(run_dir)}),
        call_id=BT, success=True)
    money = [r for r in ledger._evidence if r.field.endswith("final_value")]
    assert money and all(r.currency is None for r in money)
    assert not ledger._scope_currency


def test_buy_and_hold_cash_ledger_monthly_path_and_summary_refs(session):
    """YPFD buy-and-hold: the new engine tables and the summary.* paths ground."""
    ledger, metrics, _, engine, run_dir = session
    reads = {}
    for call, artifact in (("call_cl|fc", "artifacts/cash_ledger.csv"),
                           ("call_me|fc", "artifacts/monthly_equity.csv")):
        text = read_run_artifact(str(run_dir), artifact)
        reads[call] = json.loads(text)
        ledger.ingest_tool_result(tool_name="read_run_artifact",
                                  arguments={"run_dir": str(run_dir), "artifact": artifact},
                                  result=text, call_id=call, success=True)
    cl = reads["call_cl|fc"]
    col = cl["columns"].index
    buy, sell = cl["rows"]
    assert buy[col("total_cost")] == pytest.approx(buy[col("notional")] + buy[col("fee")])
    assert sell[col("cash_after")] == pytest.approx(metrics["final_value"])
    items = [
        (f"{_ars(1_000_000)} ARS", "capital", f"{BT}::summary.initial_cash"),
        (f"{_ars(metrics['final_value'])} ARS", "patrimonio final", f"{BT}::summary.metrics.final_value"),
        (f"{_ars(metrics['ar_net_pnl'])} ARS", "resultado neto", f"{BT}::summary.metrics.ar_net_pnl"),
        (f"{_ars(buy[col('total_cost')])} ARS", "costo de compra", f"call_cl|fc::rows[0][{col('total_cost')}]"),
        (f"{_ars(buy[col('cash_after')])} ARS", "efectivo tras comprar", f"call_cl|fc::rows[0][{col('cash_after')}]"),
        (f"{_ars(sell[col('net_proceeds')])} ARS", "venta neta", f"call_cl|fc::rows[1][{col('net_proceeds')}]"),
    ]
    me = reads["call_me|fc"]
    for row, values in enumerate(me["rows"]):
        items.append((f"{_ars(values[me['columns'].index('equity_end')])} ARS", f"fin de {values[1]}",
                      f"call_me|fc::rows[{row}][{me['columns'].index('equity_end')}]"))
    result = ledger.validate_final_answer(_answer(*items))
    assert result.valid, _reasons(result)
