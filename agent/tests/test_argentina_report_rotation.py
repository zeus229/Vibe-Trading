"""YPFD.BA -> GGAL.BA rotation report: cash reconciliation and fees.

Production 2026-10-26 (session d7a07a87234e, run 20261026_032054_89_d4d3f7):
the rotation ran (+21.37%), but seven figures were cut. They were all cash
reconciliation numbers (cash after the first buy, YPFD net proceeds, cash
available, GGAL cost, cash after the rotation, the gap, the gain) that the
agent computed with financial_rigor from fills.jsonl read through read_file:
a calculator result is not an observation and a JSONL read is not evidence.
Earlier drafts also cited `call::summary.metrics.*`, the paths of the backtest
result itself, which the ledger had recorded under the run card's own paths,
and an off-by-one `rows[61]` month-end that the gate rightly rejected.

The engine now writes `artifacts/cash_ledger.csv` (per fill: amount, fee,
total cost / net proceeds, cash flow and balance, settlement date, buying
power before a purchase, unsettled proceeds) and the equity table;
`summary.*` refs resolve to the same run-card values. The same config and
signal_engine.py the agent wrote are run here through runner.main on
synthetic bars (real 2024 holiday gaps); no network.
"""

from __future__ import annotations

import io
import json
import textwrap
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

from backtest import runner
from backtest.engines.argentina_equity import ArgentinaEquityEngine
from src.agent.grounding import GroundingLedger
from src.tools.backtest_summary import try_build_backtest_summary
from src.tools.run_artifact_tool import read_run_artifact

CONFIG = {  # as written by the agent in production
    "source": "yahoo", "codes": ["YPFD.BA", "GGAL.BA"],
    "start_date": "2024-05-31", "end_date": "2024-09-30",
    "evaluation_start_date": "2024-06-01", "interval": "1D",
    "initial_cash": 1000000, "commission": 0.001, "engine": "daily",
    "position_adjustment": "rebalance", "rebalance_mask": ["2024-06-01", "2024-08-01"],
    "rebalance_tolerance": 0.0,
    "strategy_params": {"initial_symbol": "YPFD.BA", "rotation_symbol": "GGAL.BA",
                        "switch_date": "2024-08-01", "max_allocation": 0.95},
}
SIGNAL = textwrap.dedent('''
    import json
    from pathlib import Path
    from typing import Dict

    import pandas as pd


    class SignalEngine:
        def __init__(self):
            config_path = Path(__file__).resolve().parents[1] / "config.json"
            with config_path.open("r", encoding="utf-8") as handle:
                config = json.load(handle)
            params = config["strategy_params"]
            self.initial_symbol = params["initial_symbol"]
            self.rotation_symbol = params["rotation_symbol"]
            self.switch_date = pd.Timestamp(params["switch_date"])
            self.max_allocation = float(params["max_allocation"])

        def generate(self, data_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
            signals: Dict[str, pd.Series] = {}
            for code, frame in data_map.items():
                index = frame.index
                next_session = pd.Series(index, index=index).shift(-1)
                intended_execution = next_session.fillna(pd.Series(index, index=index))
                signal = pd.Series(0.0, index=index, dtype=float)
                if code == self.initial_symbol:
                    signal.loc[intended_execution < self.switch_date] = self.max_allocation
                elif code == self.rotation_symbol:
                    signal.loc[intended_execution >= self.switch_date] = self.max_allocation
                signals[code] = signal
            return signals
''')
HOLIDAYS = {"2024-06-17", "2024-06-20", "2024-06-21", "2024-07-09", "2024-08-19"}
DAYS = [d for d in pd.bdate_range("2024-05-31", "2024-09-30") if d.strftime("%Y-%m-%d") not in HOLIDAYS]


def _frame(base: float, drift: float) -> pd.DataFrame:
    closes = [round(base * (1 + drift) ** i + 7 * ((i * 13) % 5), 2) for i in range(len(DAYS))]
    opens = [round(c * 0.997, 2) for c in closes]
    frame = pd.DataFrame({"open": opens, "high": [c * 1.01 for c in closes],
                          "low": [c * 0.99 for c in closes], "close": closes,
                          "volume": [250_000.0] * len(DAYS)}, index=pd.DatetimeIndex(DAYS))
    frame.attrs["quote_currency"] = "ARS"
    return frame


FRAMES = {"YPFD.BA": _frame(2900.0, -0.0025), "GGAL.BA": _frame(3500.0, 0.004)}


class _Loader:
    name = "yahoo"

    def fetch(self, codes, *args, **kwargs):
        out = {}
        for code in codes:
            out[code] = FRAMES[code].copy()
            out[code].attrs = dict(FRAMES[code].attrs)
        return out


@pytest.fixture()
def rotation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    monkeypatch.delenv("VIBE_TRADING_DATA_CACHE", raising=False)
    monkeypatch.setattr(runner, "_get_loader", lambda source: _Loader)
    monkeypatch.setattr(ArgentinaEquityEngine, "describe_instrument",
                        staticmethod(lambda s: {"YPFD.BA": "YPF SOCIEDAD ANONIMA",
                                                "GGAL.BA": "GRUPO FINANCIERO GALICIA SA"}[s]))
    run_dir = tmp_path / "run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    (run_dir / "code" / "signal_engine.py").write_text(SIGNAL, encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        runner.main(run_dir)
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Rotación YPFD.BA a GGAL.BA en BYMA")
    payload = {"status": "ok", "exit_code": 0, "stdout": "", "run_dir": str(run_dir),
               "summary": try_build_backtest_summary(run_dir, {})}
    ledger.ingest_tool_result(tool_name="backtest", arguments={"run_dir": str(run_dir)},
                              result=json.dumps(payload), call_id="bt", success=True)
    reads = {}
    for call, artifact in (("cl", "artifacts/cash_ledger.csv"), ("eq", "equity")):
        text = read_run_artifact(str(run_dir), artifact)
        data = json.loads(text)
        assert "rows" in data, data
        ledger.ingest_tool_result(tool_name="read_run_artifact",
                                  arguments={"run_dir": str(run_dir), "artifact": artifact},
                                  result=text, call_id=call, success=True)
        reads[call] = data
    return ledger, payload["summary"], reads, run_dir


def _ars(value: float) -> str:
    whole, frac = f"{abs(value):,.2f}".split(".")
    return ("-" if value < 0 else "") + whole.replace(",", ".") + "," + frac


def _pct(value: float, places: int = 2) -> str:
    return f"{value * 100:.{places}f}".replace(".", ",") + "%"


def _answer(*items) -> str:
    prose = "\n".join(f"- {label}: {text}" for text, label, _ in items)
    block = "\n".join(f"{text} | observed | {label} | {ref}" for text, label, ref in items)
    return prose + "\n\n```figures\n" + block + "\n```"


def _cell(reads, call, row, column):
    columns = reads[call]["columns"]
    return reads[call]["rows"][row][columns.index(column)], f"{call}::rows[{row}][{columns.index(column)}]"


def _reasons(result):
    return [(i.get("value"), i.get("reason")) for i in result.issues if i.get("value") is not None]


# ── what the run itself shows ──

def test_rotation_trades_and_t1_matched_funding(rotation):
    _, summary, reads, _ = rotation
    rows = [dict(zip(reads["cl"]["columns"], r)) for r in reads["cl"]["rows"]]
    assert [(r["timestamp"], r["code"], r["side"]) for r in rows] == [
        ("2024-06-03", "YPFD.BA", "buy"), ("2024-08-01", "YPFD.BA", "sell"),
        ("2024-08-01", "GGAL.BA", "buy"), ("2024-09-30", "GGAL.BA", "sell")]
    sale, buy = rows[1], rows[2]
    # Normal T+1 (after 2024-05-27): both settle 2024-08-02; the sale funds the buy.
    assert sale["settlement_date"] == buy["settlement_date"] == "2024-08-02"
    assert buy["buying_power_before"] == pytest.approx(rows[0]["cash_after"] + sale["net_proceeds"])
    assert buy["total_cost"] <= buy["buying_power_before"]
    assert buy["total_cost"] > rows[0]["cash_after"]  # settled cash alone would not suffice
    # Ledger closes on the engine's final value and fee totals.
    assert rows[-1]["cash_after"] == pytest.approx(summary["metrics"]["final_value"])
    assert sum(r["fee"] for r in rows) == pytest.approx(summary["metrics"]["ar_total_fees"])
    assert summary["metrics"]["ar_net_pnl"] == pytest.approx(summary["metrics"]["final_value"] - 1_000_000)


def test_first_buy_uses_the_warmup_bars_signal_at_the_next_open_without_lookahead(rotation):
    """05-31 is a warm-up bar: its signal (data <= 05-31 close) executes at the
    06-03 open, the first evaluated bar. The fill price is that open, not a close."""
    _, _, reads, run_dir = rotation
    fills = [json.loads(line) for line in (run_dir / "artifacts" / "fills.jsonl").read_text().splitlines()]
    first = fills[0]
    assert first["timestamp"].startswith("2024-06-03")
    open_0603 = FRAMES["YPFD.BA"].loc["2024-06-03", "open"]
    close_0603 = FRAMES["YPFD.BA"].loc["2024-06-03", "close"]
    assert first["execution_price"] >= open_0603 and first["execution_price"] < close_0603
    rotation_fills = [f for f in fills if f["timestamp"].startswith("2024-08-01")]
    assert {f["symbol"] for f in rotation_fills} == {"YPFD.BA", "GGAL.BA"}
    equity = reads["eq"]["rows"]
    assert equity[0][0] == "2024-06-03"  # no evaluated bar before the evaluation start


# ── the report now grounds ──

def test_report_figures_cut_in_production_now_ground(rotation):
    ledger, summary, reads, _ = rotation
    m = summary["metrics"]
    items = [
        (f"{_ars(1_000_000)} ARS", "capital inicial", "bt::summary.initial_cash"),
        (f"{_ars(m['final_value'])} ARS", "patrimonio final", "bt::summary.metrics.final_value"),
        (f"{_ars(m['ar_net_pnl'])} ARS", "ganancia neta", "bt::summary.metrics.ar_net_pnl"),
        (_pct(m["total_return"]), "rendimiento neto", "bt::summary.metrics.total_return"),
        (f"{_ars(m['ar_total_fees'])} ARS", "comisiones totales", "bt::summary.metrics.ar_total_fees"),
        (f"{_ars(m['ar_buy_fees'])} ARS", "comisiones de compra", "bt::summary.metrics.ar_buy_fees"),
        (f"{_ars(m['ar_sell_fees'])} ARS", "comisiones de venta", "bt::summary.metrics.ar_sell_fees"),
    ]
    for label, row, column in (
        ("efectivo tras la compra inicial", 0, "cash_after"),
        ("venta neta de YPFD", 1, "net_proceeds"),
        ("efectivo disponible tras la venta", 1, "cash_after"),
        ("poder de compra para GGAL", 2, "buying_power_before"),
        ("costo total de GGAL", 2, "total_cost"),
        ("efectivo tras la rotación", 2, "cash_after"),
        ("comisión de la compra de GGAL", 2, "fee"),
        ("venta pendiente de liquidar", 2, "unsettled_sale_proceeds_after"),
    ):
        value, ref = _cell(reads, "cl", row, column)
        items.append((f"{_ars(value)} ARS", label, ref))
    result = ledger.validate_final_answer(_answer(*items))
    assert result.valid, _reasons(result)


# ── what stays closed ──

def test_off_by_one_month_end_is_rejected(rotation):
    """Production cited rows[61] (2024-09-02) for the August month-end."""
    ledger, _, reads, _ = rotation
    august = next(i for i, r in enumerate(reads["eq"]["rows"])
                  if r[0].startswith("2024-08") and not reads["eq"]["rows"][i + 1][0].startswith("2024-08"))
    value, _ = _cell(reads, "eq", august, "equity")
    _, wrong_ref = _cell(reads, "eq", august + 1, "equity")
    assert not ledger.validate_final_answer(_answer((f"{_ars(value)} ARS", "agosto", wrong_ref))).valid
    _, right_ref = _cell(reads, "eq", august, "equity")
    assert ledger.validate_final_answer(_answer((f"{_ars(value)} ARS", "agosto", right_ref))).valid


def test_reconciliation_values_cannot_be_borrowed_or_invented(rotation):
    ledger, _, reads, _ = rotation
    power, _ = _cell(reads, "cl", 2, "buying_power_before")
    _, cost_ref = _cell(reads, "cl", 2, "total_cost")
    assert not ledger.validate_final_answer(_answer((f"{_ars(power)} ARS", "x", cost_ref))).valid
    _, power_ref = _cell(reads, "cl", 2, "buying_power_before")
    assert not ledger.validate_final_answer(_answer((f"{_ars(power + 0.01)} ARS", "x", power_ref))).valid
    assert not ledger.validate_final_answer(_answer((f"{_ars(power)} USD", "x", power_ref))).valid


def test_a_calculator_result_is_still_not_an_observation(rotation):
    ledger, _, reads, _ = rotation
    cash, _ = _cell(reads, "cl", 0, "cash_after")
    ledger.ingest_tool_result(
        tool_name="financial_rigor", arguments={"command": "calc", "expr": f"{cash} + 0"},
        result=json.dumps({"status": "ok", "command": "calc", "result": cash, "result_exact": str(cash)}),
        call_id="calc", success=True)
    assert not ledger.validate_final_answer(
        _answer((f"{_ars(cash)} ARS", "efectivo", "calc::result"))).valid


def test_summary_alias_only_names_run_card_values(rotation):
    ledger, summary, _, _ = rotation
    m = summary["metrics"]
    # Right value, wrong summary field.
    assert not ledger.validate_final_answer(_answer(
        (f"{_ars(m['final_value'])} ARS", "x", "bt::summary.metrics.ar_total_fees"))).valid
    # summary.* of a call that is not a backtest resolves nothing.
    assert not ledger.validate_final_answer(_answer(
        (f"{_ars(m['final_value'])} ARS", "x", "cl::summary.metrics.final_value"))).valid


# ── per-symbol and per-exit-reason P&L (production 2026-10-26, run 20261026_155814_36_c86ce4) ──
# The agent quoted GGAL's gross gain "ARS 318.920,00" from
# `bt::structured_metrics.by_symbol.GGAL.BA.total_pnl`. The value was in that call, but
# only the run card's headline amounts carried the run's ARS tag, so the currency
# filter dropped the untagged per-symbol amount and the figure was cut as
# "not_in_referenced_call".

def _structured(run_dir):
    return json.loads((run_dir / "run_card.json").read_text())["structured_metrics"]


def test_per_symbol_and_exit_reason_pnl_ground_in_the_runs_currency(rotation):
    ledger, _, _, run_dir = rotation
    structured = _structured(run_dir)
    items = []
    for symbol in ("YPFD.BA", "GGAL.BA"):
        stats = structured["by_symbol"][symbol]
        items.append((f"{_ars(stats['total_pnl'])} ARS", f"resultado bruto {symbol}",
                      f"bt::structured_metrics.by_symbol.{symbol}.total_pnl"))
        items.append((f"{_ars(stats['avg_pnl'])} ARS", f"resultado medio {symbol}",
                      f"bt::structured_metrics.by_symbol.{symbol}.avg_pnl"))
    for reason, stats in structured["by_exit_reason"].items():
        items.append((f"{_ars(stats['total_pnl'])} ARS", f"resultado por {reason}",
                      f"bt::structured_metrics.by_exit_reason.{reason}.total_pnl"))
    result = ledger.validate_final_answer(_answer(*items))
    assert result.valid, _reasons(result)


def test_pnl_figures_stay_closed_to_other_currencies_symbols_and_invented_values(rotation):
    ledger, _, _, run_dir = rotation
    structured = _structured(run_dir)
    ggal = structured["by_symbol"]["GGAL.BA"]["total_pnl"]
    ypfd = structured["by_symbol"]["YPFD.BA"]["total_pnl"]
    assert ggal != ypfd
    ref = "bt::structured_metrics.by_symbol.GGAL.BA.total_pnl"
    assert ledger.validate_final_answer(_answer((f"{_ars(ggal)} ARS", "x", ref))).valid
    assert not ledger.validate_final_answer(_answer((f"{_ars(ggal)} USD", "x", ref))).valid
    assert not ledger.validate_final_answer(_answer((f"{_ars(ggal + 1)} ARS", "x", ref))).valid
    # The other instrument's amount cannot be borrowed through GGAL's path.
    assert not ledger.validate_final_answer(_answer((f"{_ars(ypfd)} ARS", "x", ref))).valid
