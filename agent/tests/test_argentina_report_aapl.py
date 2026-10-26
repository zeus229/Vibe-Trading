"""AAPL.BA buy-and-hold across the 2024 ratio change: settlement tail and report.

Production 2026-10-26 (session fc6833de0626, run 20261026_113342_44_5e17c3):
- the 2024-03-27 sale was reported settling 2024-03-29: the derived calendar's
  tail after the last bar assumed weekdays, but BYMA was closed 03-28 .. 04-02
  and T+2 applied, so it settles 2024-04-04;
- six figures were cut: fee rates and ratio fields cited from
  `read_run_artifact(byma_preparation.json)` as `call::json.*` (the file was
  not evidence), an undeclared month-end equity, and two execution prices
  (adjusted CEDEAR units; prices stay excluded by policy).

The agent's own config runs here through runner.main on synthetic bars with
the real 2024 holidays; no network.
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
    "source": "yahoo", "codes": ["AAPL.BA"], "start_date": "2024-01-02", "end_date": "2024-03-27",
    "warmup_bars": 0, "interval": "1D", "initial_cash": 1000000, "commission": 0.001,
    "engine": "daily", "position_adjustment": "hold",
    "ar_instruments": {"AAPL.BA": {
        "kind": "cedear", "underlying": "AAPL.US",
        "ratio_history": [
            {"effective_date": "2024-01-02", "ratio": "10", "credit_date": "2024-01-02"},
            {"effective_date": "2024-01-24", "ratio": "20", "credit_date": "2024-01-26"},
        ]}},
}
SIGNAL = textwrap.dedent('''
    import pandas as pd


    class SignalEngine:
        def generate(self, data_map):
            return {c: pd.Series(0.95, index=d.index) for c, d in data_map.items()}
''')


def _bars(start, end, closed, pre_price, ex=None, factor=1.0):
    days = [d for d in pd.bdate_range(start, end) if d.strftime("%Y-%m-%d") not in closed]
    rows = []
    for i, day in enumerate(days):
        price = pre_price * (1 + 0.003 * ((i * 7) % 5 - 2))
        if ex is not None and day >= pd.Timestamp(ex):
            price *= factor
        rows.append(round(price, 1))
    frame = pd.DataFrame({"open": rows, "high": [p * 1.01 for p in rows], "low": [p * 0.99 for p in rows],
                          "close": rows, "volume": [50_000.0] * len(rows)}, index=pd.DatetimeIndex(days))
    frame.attrs["quote_currency"] = "ARS"
    return frame


AAPL = _bars("2024-01-02", "2024-03-27", {"2024-02-12", "2024-02-13"}, 18500.0, "2024-01-24", 0.5)


def _run(tmp_path, monkeypatch, config, frame, code):
    class Loader:
        name = "yahoo"

        def fetch(self, codes, *a, **k):
            out = frame.copy()
            out.attrs = dict(frame.attrs)
            return {code: out}

    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    monkeypatch.delenv("VIBE_TRADING_DATA_CACHE", raising=False)
    monkeypatch.setattr(runner, "_get_loader", lambda source: Loader)
    run_dir = tmp_path / "run"
    (run_dir / "code").mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (run_dir / "code" / "signal_engine.py").write_text(SIGNAL, encoding="utf-8")
    with redirect_stdout(io.StringIO()):
        runner.main(run_dir)
    return run_dir


@pytest.fixture()
def aapl(tmp_path: Path, monkeypatch):
    run_dir = _run(tmp_path, monkeypatch, CONFIG, AAPL, "AAPL.BA")
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Backtest de AAPL.BA")
    summary = try_build_backtest_summary(run_dir, {})
    ledger.ingest_tool_result(
        tool_name="backtest", arguments={"run_dir": str(run_dir)},
        result=json.dumps({"status": "ok", "exit_code": 0, "run_dir": str(run_dir), "summary": summary}),
        call_id="bt", success=True)
    reads = {}
    for call, artifact in (("cl", "artifacts/cash_ledger.csv"), ("me", "artifacts/monthly_equity.csv"),
                           ("bp", "artifacts/byma_preparation.json"), ("tr", "trades")):
        text = read_run_artifact(str(run_dir), artifact)
        ledger.ingest_tool_result(tool_name="read_run_artifact",
                                  arguments={"run_dir": str(run_dir), "artifact": artifact},
                                  result=text, call_id=call, success=True)
        reads[call] = json.loads(text)
    return ledger, summary, reads, run_dir


def _ars(v):
    whole, frac = f"{abs(v):,.2f}".split(".")
    return ("-" if v < 0 else "") + whole.replace(",", ".") + "," + frac


def _pct(v, places=4):
    return f"{v * 100:.{places}f}".replace(".", ",") + "%"


def _answer(*items):
    prose = "\n".join(f"- {label}: {text}" for text, label, _ in items)
    return prose + "\n\n```figures\n" + "\n".join(
        f"{text} | observed | {label} | {ref}" for text, label, ref in items) + "\n```"


def _reasons(result):
    return [(i.get("value"), i.get("reason")) for i in result.issues if i.get("value") is not None]


# ── settlement calendar ──

def test_final_sale_settles_after_the_official_easter_closure(aapl):
    _, _, reads, run_dir = aapl
    rows = [dict(zip(reads["cl"]["columns"], r)) for r in reads["cl"]["rows"]]
    buy, sell = rows
    assert (buy["timestamp"], buy["settlement_date"]) == ("2024-01-03", "2024-01-05")
    # T+2 from 03-27 skipping 03-28, 03-29, 04-01, 04-02 (official 2024 closures).
    assert (sell["timestamp"], sell["settlement_date"]) == ("2024-03-27", "2024-04-04")
    assert sell["settlement_date_status"] == "derived"
    prep = reads["bp"]["json"]
    assert prep["ar_calendar_provenance"]["unverified_tail"] == []
    assert "tail: official business days" in prep["ar_data_basis"]


def test_undocumented_year_reports_the_tail_settlement_as_unknown(tmp_path, monkeypatch):
    """2023 has no official closure table here: the final sale's settlement
    falls beyond the loaded bars and is not reported as a date."""
    monkeypatch.setattr(ArgentinaEquityEngine, "describe_instrument",
                        staticmethod(lambda s: "YPF SOCIEDAD ANONIMA"))
    frame = _bars("2023-11-01", "2023-12-28", {"2023-11-20", "2023-12-08", "2023-12-25"}, 15000.0)
    config = {"source": "yahoo", "codes": ["YPFD.BA"], "start_date": "2023-11-01",
              "end_date": "2023-12-28", "initial_cash": 1000000, "commission": 0.001,
              "position_adjustment": "hold"}
    run_dir = _run(tmp_path, monkeypatch, config, frame, "YPFD.BA")
    ledger = (run_dir / "artifacts" / "cash_ledger.csv").read_text().splitlines()
    header = ledger[0].split(",")
    sell = dict(zip(header, ledger[-1].split(",")))
    assert sell["side"] == "sell" and sell["settlement_date"] == ""
    assert sell["settlement_date_status"] == "unknown_beyond_loaded_data"
    prep = json.loads((run_dir / "artifacts" / "byma_preparation.json").read_text())
    assert prep["ar_calendar_provenance"]["unverified_tail"][0] == "2023-12-29"


def test_traded_bar_on_an_official_closure_fails_closed():
    from backtest.engines.argentina_metadata import observed_sessions

    frame = _bars("2024-03-26", "2024-03-28", set(), 100.0)  # 03-28 was closed
    with pytest.raises(ValueError, match="officially closed"):
        observed_sessions([frame])


# ── report figures ──

def test_ar_data_basis_and_monthly_figures_cut_in_production_now_ground(aapl):
    ledger, summary, reads, _ = aapl
    prep = reads["bp"]["json"]
    adj = prep["ar_cedear_ratio_adjustments"][0]
    items = [
        (_pct(prep["ar_fee_basis"]["brokerage_rate"]), "corretaje", "bp::json.ar_fee_basis.brokerage_rate"),
        (_pct(prep["ar_fee_basis"]["exchange_fee_rate"]), "aranceles", "bp::json.ar_fee_basis.exchange_fee_rate"),
        (f"{adj['old_ratio']:.0f}", "ratio anterior", "bp::json.ar_cedear_ratio_adjustments[0].old_ratio"),
        (f"{adj['new_ratio']:.0f}", "ratio nuevo", "bp::json.ar_cedear_ratio_adjustments[0].new_ratio"),
        (f"{adj['observed_move']:.6f}".replace(".", ","), "movimiento observado",
         "bp::json.ar_cedear_ratio_adjustments[0].observed_move"),
        (_pct(prep["ar_fee_basis"]["brokerage_rate"]), "corretaje (run)", "bt::ar_fee_basis.brokerage_rate"),
        (f"{_ars(summary['metrics']['final_value'])} ARS", "patrimonio final", "bt::summary.metrics.final_value"),
    ]
    me = reads["me"]
    col = me["columns"].index("equity_end")
    for row, values in enumerate(me["rows"]):
        items.append((f"{_ars(values[col])} ARS", f"fin {values[1]}", f"me::rows[{row}][{col}]"))
    result = ledger.validate_final_answer(_answer(*items))
    assert result.valid, _reasons(result)


def test_execution_prices_stay_excluded(aapl):
    """Adjusted CEDEAR units: a fill price is not a market price and is not cited."""
    ledger, _, reads, _ = aapl
    price_col = reads["tr"]["columns"].index("price")
    price = reads["tr"]["rows"][0][price_col]
    result = ledger.validate_final_answer(_answer((_ars(price), "precio", f"tr::rows[0][{price_col}]")))
    assert not result.valid


def test_json_paths_name_only_what_was_shown(aapl):
    ledger, _, reads, _ = aapl
    prep = reads["bp"]["json"]
    rate = _pct(prep["ar_fee_basis"]["brokerage_rate"])
    # Right value, wrong field.
    assert not ledger.validate_final_answer(_answer((rate, "x", "bp::json.ar_fee_basis.slippage"))).valid
    # json.* of a call that read something else resolves nothing.
    assert not ledger.validate_final_answer(_answer((rate, "x", "me::json.ar_fee_basis.brokerage_rate"))).valid


def test_a_modified_preparation_file_is_not_evidence(tmp_path, monkeypatch):
    run_dir = _run(tmp_path, monkeypatch, CONFIG, AAPL, "AAPL.BA")
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Backtest de AAPL.BA")
    ledger.ingest_tool_result(tool_name="backtest", arguments={"run_dir": str(run_dir)},
                              result=json.dumps({"status": "ok", "exit_code": 0, "run_dir": str(run_dir)}),
                              call_id="bt", success=True)
    path = run_dir / "artifacts" / "byma_preparation.json"
    data = json.loads(path.read_text())
    data["ar_fee_basis"]["brokerage_rate"] = 0.0005  # tampered after the run
    path.write_text(json.dumps(data))
    text = read_run_artifact(str(run_dir), "artifacts/byma_preparation.json")
    ledger.ingest_tool_result(tool_name="read_run_artifact",
                              arguments={"run_dir": str(run_dir), "artifact": "artifacts/byma_preparation.json"},
                              result=text, call_id="bp", success=True)
    assert not ledger.validate_final_answer(
        _answer(("0,0500%", "corretaje", "bp::json.ar_fee_basis.brokerage_rate"))).valid


# ── 2026-10-26 second UI run (session b7289a95f7ea, run 20261026_123840_32_466741) ──
# The draft's ratio table "| Ratio anterior | 10:1 |" made the scanner read the
# ":1" as a measured figure "1"; the agent mis-declared it, then removed the
# ratios, and the gate fell back to an English "could not lock the instrument
# identity" text although the backtest had completed and identity was fine.

def test_unit_ratio_notation_reads_as_one_figure(aapl):
    ledger, _, reads, _ = aapl
    adj = reads["bp"]["json"]["ar_cedear_ratio_adjustments"][0]
    draft = (
        "| Campo | Valor |\n|---|---:|\n"
        f"| Ratio anterior | {adj['old_ratio']:.0f}:1 |\n"
        f"| Ratio nuevo | {adj['new_ratio']:.0f}:1 |\n\n"
        "```figures\n"
        f"{adj['old_ratio']:.0f} | observed | ratio anterior | bp::json.ar_cedear_ratio_adjustments[0].old_ratio\n"
        f"{adj['new_ratio']:.0f} | observed | ratio nuevo | bp::json.ar_cedear_ratio_adjustments[0].new_ratio\n"
        "```"
    )
    result = ledger.validate_final_answer(draft)
    assert result.valid, _reasons(result)


def test_unit_ratio_numerator_still_needs_evidence(aapl):
    ledger, _, _, _ = aapl
    draft = ("| Campo | Valor |\n|---|---:|\n| Ratio nuevo | 25:1 |\n\n```figures\n"
             "25 | observed | ratio | bp::json.ar_cedear_ratio_adjustments[0].new_ratio\n```")
    assert not ledger.validate_final_answer(draft).valid


def test_full_report_of_the_second_run_grounds(aapl):
    ledger, summary, reads, _ = aapl
    m = summary["metrics"]
    cl = reads["cl"]
    col = cl["columns"].index
    adj = reads["bp"]["json"]["ar_cedear_ratio_adjustments"][0]
    items = [
        (f"{_ars(1_000_000)} ARS", "capital", "bt::summary.initial_cash"),
        (f"{_ars(m['final_value'])} ARS", "patrimonio final", "bt::summary.metrics.final_value"),
        (_pct(m["total_return"], 4), "rendimiento", "bt::summary.metrics.total_return"),
        (f"{_ars(m['ar_total_fees'])} ARS", "comisiones", "bt::summary.metrics.ar_total_fees"),
        (f"{_ars(cl['rows'][0][col('total_cost')])} ARS", "costo compra", f"cl::rows[0][{col('total_cost')}]"),
        (f"{_ars(cl['rows'][1][col('net_proceeds')])} ARS", "venta neta", f"cl::rows[1][{col('net_proceeds')}]"),
        (f"{adj['old_ratio']:.0f}", "ratio anterior", "bp::json.ar_cedear_ratio_adjustments[0].old_ratio"),
        (f"{adj['new_ratio']:.0f}", "ratio nuevo", "bp::json.ar_cedear_ratio_adjustments[0].new_ratio"),
    ]
    me = reads["me"]
    for row, values in enumerate(me["rows"]):
        c = me["columns"].index("equity_end")
        items.append((f"{_ars(values[c])} ARS", f"fin {values[1]}", f"me::rows[{row}][{c}]"))
    body = _answer(*items).replace(
        "```figures", f"| Ratio | {adj['old_ratio']:.0f}:1 → {adj['new_ratio']:.0f}:1 |\n|---|---|\n\n```figures", 1)
    result = ledger.validate_final_answer(body)
    assert result.valid, _reasons(result)
    assert cl["rows"][1][col("settlement_date")] == "2024-04-04"


def _fallback_ledger(tmp_path, message, issues, *, completed=True):
    ledger = GroundingLedger(run_dir=tmp_path, user_message=message)
    if completed:
        ledger._analysis_completed.append({"call_id": "bt", "tool": "backtest", "recorded_at": "x"})
    ledger._validations.append({"issues": issues})
    return ledger


def test_fallback_after_a_completed_backtest_names_the_figure_check_in_spanish(tmp_path):
    prompt = ("Ejecutá un backtest histórico buy-and-hold de AAPL.BA desde el 02/01/2024 hasta "
              "el 27/03/2024. Mostrame rendimiento neto, patrimonio final y comisiones.")
    issues = [{"code": "correction_contract_violation", "reason": "required_figure_removed"}]
    text = _fallback_ledger(tmp_path, prompt, issues).safe_fallback()
    assert text.startswith("Mi respuesta no pasó la verificación de cifras")
    assert "identidad" not in text


def test_identity_fallback_is_kept_for_identity_issues_and_other_languages(tmp_path):
    prompt_es = "Ejecutá un backtest de AAPL.BA y mostrame el patrimonio con las comisiones"
    identity = [{"code": "identity_mismatch", "reason": "identity_mismatch"}]
    assert "identidad del instrumento" in _fallback_ledger(tmp_path, prompt_es, identity).safe_fallback()
    issues = [{"code": "correction_contract_violation", "reason": "required_figure_removed"}]
    english = _fallback_ledger(tmp_path, "Run a buy-and-hold backtest of AAPL", issues).safe_fallback()
    assert english.startswith("My answer did not pass the figure check")
    no_run = _fallback_ledger(tmp_path, "Run a buy-and-hold backtest of AAPL", issues, completed=False)
    assert no_run.safe_fallback().startswith("I could not safely lock the instrument identity")


@pytest.mark.parametrize("text,expected", [
    ("| Ratio | Valor |\n|---|---|\n| nuevo | 20:1 |", [("20", "measured"), ("1", "exempt")]),
    ("el ratio pasó de 10:1 a 20:1", [("10", "bare"), ("1", "exempt"), ("20", "bare"), ("1", "exempt")]),
    ("reunión a las 10:15", [("10", "exempt"), ("15", "exempt")]),
    ("ratio 1:2", [("1", "bare"), ("2", "bare")]),
    ("| Ratio | Valor |\n|---|---|\n| x | 1:2 |", [("1", "measured"), ("2", "measured")]),
])
def test_scanner_exempts_only_the_unit_denominator_of_a_ratio(text, expected):
    from src.agent.grounding.figures import parse_figures_block, scan_figures

    parsed = parse_figures_block(text)
    block = parsed[1] if isinstance(parsed, tuple) else parsed
    assert [(f.text, f.shape) for f in scan_figures(text, block)] == expected
