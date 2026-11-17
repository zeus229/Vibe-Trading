"""Joint ACCIONES + CEDEARS Risk X-Ray through the Asistente Casa adapter.

The adapter only prepares data (one capture, ARS closes, N+1 closes for N returns,
coverage reporting); every statistic still comes from the native
``backtest.risk_xray.compute_risk_xray``.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from backtest.risk_xray import compute_risk_xray
from src.agent.instrument_identity import IdentityLedger
from src.tools import asistente_casa_portfolio_risk_tool as risk_module
from src.tools.asistente_casa_portfolio_risk_tool import AsistenteCasaPortfolioRiskXrayTool

TOTAL = 1_000_000.0
# symbol -> (instrument_type, market value ARS)
HOLDINGS = {
    "AAA": ("ACCIONES", 300_000.0),
    "BBB": ("ACCIONES", 200_000.0),
    "CCC": ("CEDEARS", 350_000.0),
    "DDD": ("CEDEARS", 50_000.0),
    "BOND": ("BONOS", 60_000.0),
    "FUND": ("FCI", 30_000.0),  # 10_000 stays as cash / other
}
DATES = pd.bdate_range("2026-01-02", periods=80)


def _closes() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(7)
    return {
        s: 1000.0 * np.cumprod(1 + rng.normal(0.0005, 0.02, len(DATES)))
        for s in HOLDINGS
    }


CLOSES = _closes()


def _positions() -> list[dict[str, object]]:
    return [
        {
            "symbol": s,
            "name": s,
            "instrument_type": kind,
            "market_value_ars": value,
            "weight_total_portfolio": value / TOTAL,
            "weight_scope": 0.0,  # per-scope weights must be recomputed, not trusted
        }
        for s, (kind, value) in HOLDINGS.items()
    ]


def _series(kind: str, *, currency: str | None = "ARS", symbols=None) -> dict[str, object]:
    out = {}
    for s, (k, _) in HOLDINGS.items():
        if k != kind or (symbols is not None and s not in symbols):
            continue
        out[s] = {
            "currency": currency,
            "observations": [
                {
                    "date": d.date().isoformat(),
                    "close": float(c),
                    "currency": currency,
                    "source": "ppi",
                    "quality": "valid",
                }
                for d, c in zip(DATES, CLOSES[s])
            ],
        }
    return out


def _install(monkeypatch, *, history=None, calls=None, currency="ARS"):
    monkeypatch.setenv("ASISTENTE_CASA_BASE_URL", "http://ac.local")
    monkeypatch.setenv("ASISTENTE_CASA_API_KEY", "secret")
    calls = calls if calls is not None else []

    def fake_get(base_url, api_key, path, params):
        calls.append((path, dict(params)))
        if path == "/inversiones/vibe/portfolio":
            assert params["asset_type"] == "ALL"  # one capture for the whole portfolio
            return {
                "ok": True,
                "contract_version": "t",
                "position_snapshot_id": "snap",
                "portfolio": {
                    "positions": _positions(),
                    "total_value_ars": TOTAL,
                    "scope_value_ars": TOTAL,
                },
            }
        if path == "/inversiones/vibe/market-history/coverage":
            return {
                "ok": True,
                "numeric_format": "json_number",
                "policy": "persisted_only",
                "earliest_common_date": DATES[0].date().isoformat(),
            }
        if path == "/inversiones/vibe/market-history":
            kind = params["asset_type"]
            series = (history or (lambda k: _series(k, currency=currency)))(kind)
            return {
                "ok": True,
                "numeric_format": "json_number",
                "policy": "persisted_only",
                "interval": "1D",
                "complete": True,
                "unresolved_symbols": [],
                "unsafe_symbols": [],
                "contract_version": "h",
                "series": series,
            }
        raise AssertionError(path)

    monkeypatch.setattr(risk_module, "_get", fake_get)
    return calls


def _run(**kwargs) -> dict:
    return json.loads(
        AsistenteCasaPortfolioRiskXrayTool().execute(
            asset_type="ACCIONES+CEDEARS", end_date="2026-04-30", **kwargs
        )
    )


def test_combined_scope_matches_native_compute_risk_xray_exactly(monkeypatch):
    calls = _install(monkeypatch)
    payload = _run(lookback_sessions=63)
    assert payload["status"] == "ok"

    equities = ["AAA", "BBB", "CCC", "DDD"]
    value = sum(HOLDINGS[s][1] for s in equities)
    weights = {s: HOLDINGS[s][1] / value for s in equities}
    panel = pd.DataFrame({s: CLOSES[s] for s in equities}, index=DATES).tail(64)
    expected = compute_risk_xray(panel, weights, periods_per_year=252, min_history=30)

    data = payload["data"]
    assert data["inputs"]["return_observations"] == 63  # N returns from N+1 closes
    assert payload["meta"]["common_date_count"] == 64
    assert data["volatility"]["annualized_vol"] == pytest.approx(
        expected["volatility"]["annualized_vol"], abs=1e-12
    )
    assert data["concentration"]["hhi"] == pytest.approx(expected["concentration"]["hhi"], abs=1e-12)
    assert data["drawdown"]["max_drawdown"] == pytest.approx(
        expected["drawdown"]["max_drawdown"], abs=1e-12
    )
    got = {a["symbol"]: a["contribution_pct"] for a in data["risk_contributions"]["assets"]}
    want = {a["symbol"]: a["contribution_pct"] for a in expected["risk_contributions"]["assets"]}
    assert got == pytest.approx(want, abs=1e-9)
    # the portfolio is read once, in a single capture
    assert [c for c in calls if c[0].endswith("/portfolio")] == [
        ("/inversiones/vibe/portfolio", {"asset_type": "ALL"})
    ]


def test_combined_scope_requests_history_per_instrument_type(monkeypatch):
    calls = _install(monkeypatch)
    _run(lookback_sessions=21)
    history = [c[1] for c in calls if c[0].endswith("/market-history")]
    assert {(h["asset_type"], h["symbols"]) for h in history} == {
        ("ACCIONES", "AAA,BBB"),
        ("CEDEARS", "CCC,DDD"),
    }


def test_coverage_reports_included_excluded_and_share_of_total_portfolio(monkeypatch):
    def history(kind):
        series = _series(kind)
        if kind == "CEDEARS":  # DDD is a recent holding with only 10 bars
            series["DDD"]["observations"] = series["DDD"]["observations"][-10:]
        return series

    _install(monkeypatch, history=history)
    payload = _run(lookback_sessions=40)
    assert payload["status"] == "ok"
    cov = payload["meta"]["coverage"]

    assert {r["symbol"] for r in cov["included"]} == {"AAA", "BBB", "CCC"}
    assert [r["symbol"] for r in cov["excluded_from_statistics"]] == ["DDD"]
    assert cov["excluded_from_statistics"][0]["valid_closes_in_fetched_history"] == 10
    assert cov["covered_pct_of_total_portfolio"] == pytest.approx(85.0)  # 850k of 1M
    assert cov["excluded_pct_of_total_portfolio"] == pytest.approx(5.0)
    assert cov["out_of_scope_pct_of_total_portfolio"] == pytest.approx(9.0)  # bond + fund
    assert {r["symbol"] for r in cov["out_of_scope"]} == {"BOND", "FUND"}
    assert cov["cash_and_other_pct_of_total_portfolio"] == pytest.approx(1.0)
    assert cov["return_observations"] == 40
    assert cov["first_close_date"] < cov["first_return_date"] <= cov["last_date"]
    assert cov["history_sources"] == {"ppi": 41 * 3}
    assert payload["meta"]["coverage_mode"] == "partial_high_coverage"
    assert any("Excluded instruments" in note for note in cov["quality_notes"])


def test_non_ars_series_is_refused(monkeypatch):
    def history(kind):
        return _series(kind, currency="USD" if kind == "CEDEARS" else "ARS")

    _install(monkeypatch, history=history)
    payload = _run(lookback_sessions=21)
    assert payload["status"] == "error"
    assert "not in ARS" in payload["error"]


def test_undeclared_currency_is_refused_for_the_joint_basket(monkeypatch):
    _install(monkeypatch, currency=None)
    payload = _run(lookback_sessions=21)
    assert payload["status"] == "error"
    assert "does not declare its currency" in payload["error"]


def test_duplicate_symbol_across_instrument_types_fails_closed(monkeypatch):
    _install(monkeypatch)
    original = risk_module._get

    def dup(base_url, api_key, path, params):
        payload = original(base_url, api_key, path, params)
        if path == "/inversiones/vibe/portfolio":
            payload["portfolio"]["positions"].append(
                {"symbol": "AAA", "instrument_type": "CEDEARS", "market_value_ars": 1.0}
            )
        return payload

    monkeypatch.setattr(risk_module, "_get", dup)
    payload = _run(lookback_sessions=21)
    assert payload["status"] == "error"
    assert "more than one instrument type" in payload["error"]


def test_interior_gap_is_counted_as_a_multi_session_return(monkeypatch):
    def history(kind):
        series = _series(kind)
        if kind == "ACCIONES":  # AAA lacks one interior bar -> that date drops from the shared panel
            obs = series["AAA"]["observations"]
            del obs[60]
        return series

    _install(monkeypatch, history=history)
    payload = _run(lookback_sessions=40)
    assert payload["status"] == "ok"
    coverage = payload["meta"]["coverage"]
    assert coverage["bridged_returns"] == 1
    detail = coverage["bridged_return_details"][0]  # the return is identified, not just counted
    assert detail["symbols_missing_on_skipped_dates"] == ["AAA"]
    assert detail["skipped_dates"] == [DATES[60].date().isoformat()]
    assert detail["return_from"] == DATES[59].date().isoformat() and detail["return_to"] == DATES[61].date().isoformat()
    assert any("span more than one session" in w for w in payload["data"]["warnings"])


def test_close_only_bars_are_reported_but_never_block_the_close_based_metrics(monkeypatch):
    def history(kind):
        series = _series(kind)
        series["CCC" if kind == "CEDEARS" else "AAA"]["observations"][-3]["quality"] = "close_only"
        return series

    _install(monkeypatch, history=history)
    payload = _run(lookback_sessions=21)
    assert payload["status"] == "ok"
    assert payload["meta"]["coverage"]["history_quality"]["close_only"] == 2


def test_single_scope_calls_keep_their_previous_contract(monkeypatch):
    calls = _install(monkeypatch)
    original = risk_module._get

    def single(base_url, api_key, path, params):
        if path == "/inversiones/vibe/portfolio":
            assert params["asset_type"] == "ACCIONES"
            payload = original(base_url, api_key, path, {"asset_type": "ALL"})
            rows = [p for p in payload["portfolio"]["positions"] if p["instrument_type"] == "ACCIONES"]
            scope = sum(p["market_value_ars"] for p in rows)
            for p in rows:
                p["weight_scope"] = p["market_value_ars"] / scope
            payload["portfolio"]["positions"] = rows
            return payload
        return original(base_url, api_key, path, params)

    monkeypatch.setattr(risk_module, "_get", single)
    payload = json.loads(
        AsistenteCasaPortfolioRiskXrayTool().execute(
            asset_type="ACCIONES", lookback_sessions=21, end_date="2026-04-30"
        )
    )
    assert payload["status"] == "ok"
    assert payload["data"]["inputs"]["return_observations"] == 21
    assert payload["meta"]["coverage"]["scope"] == "ACCIONES"


def test_unsupported_scope_is_rejected():
    payload = json.loads(AsistenteCasaPortfolioRiskXrayTool().execute(asset_type="BONOS"))
    assert payload["status"] == "error"
    assert "ACCIONES+CEDEARS" in payload["error"]


def test_tool_still_takes_no_symbol_arguments_so_identity_gate_is_not_involved(tmp_path):
    ledger = IdentityLedger(run_dir=tmp_path, user_message="riesgo de mi cartera")
    decision = ledger.authorize_tool_call(
        "asistente_casa_portfolio_risk_xray",
        {"asset_type": "ACCIONES+CEDEARS", "lookback_sessions": 252},
        batch_authorized_symbols=ledger.authorized_symbols,
        call_id="c1",
    )
    assert decision.allowed
    assert "symbols" not in AsistenteCasaPortfolioRiskXrayTool.parameters["properties"]


def test_a_recent_holding_does_not_clamp_the_start_of_long_windows(monkeypatch):
    """The coverage endpoint's earliest COMMON date follows the newest holding.

    Using it as the fetch start starved every window longer than that holding's
    history (NU: 4 bars). The earliest ANY date bounds the fetch instead and the
    explicit partial-coverage fallback excludes the short symbol.
    """
    def history(kind):
        series = _series(kind)
        if kind == "CEDEARS":
            series["DDD"]["observations"] = series["DDD"]["observations"][-5:]
        return series

    calls = _install(monkeypatch, history=history)
    original = risk_module._get

    def with_any_date(base_url, api_key, path, params):
        payload = original(base_url, api_key, path, params)
        if path == "/inversiones/vibe/market-history/coverage":
            payload["earliest_common_date"] = DATES[-5].date().isoformat()  # pulled by the newest holding
            payload["earliest_any_date"] = DATES[0].date().isoformat()
        return payload

    monkeypatch.setattr(risk_module, "_get", with_any_date)
    payload = _run(lookback_sessions=60)
    assert payload["status"] == "ok"
    assert payload["meta"]["coverage_mode"] == "partial_high_coverage"
    assert payload["data"]["inputs"]["return_observations"] == 60
    first_history = [c for c in calls if c[0].endswith("/market-history")][0][1]
    assert first_history["from"] == DATES[0].date().isoformat()  # not the newest holding's first bar


def test_coverage_percentages_state_their_denominators_in_ars(monkeypatch):
    def history(kind):
        series = _series(kind)
        if kind == "CEDEARS":
            series["DDD"]["observations"] = series["DDD"]["observations"][-10:]
        return series

    _install(monkeypatch, history=history)
    payload = _run(lookback_sessions=40)
    den = payload["meta"]["coverage"]["denominators"]
    cov = payload["meta"]["coverage"]

    assert den["total_portfolio_value_ars"] == TOTAL
    assert den["scope_value_ars"] == 900_000.0          # ACCIONES+CEDEARS only, not the whole portfolio
    assert den["included_value_ars"] == 850_000.0
    assert den["excluded_value_ars"] == 50_000.0
    assert den["out_of_scope_value_ars"] == 90_000.0    # BONOS + FCI
    # each published percentage is exactly value / its declared denominator
    assert cov["covered_pct_of_total_portfolio"] == pytest.approx(100 * den["included_value_ars"] / den["total_portfolio_value_ars"])
    assert cov["excluded_pct_of_total_portfolio"] == pytest.approx(100 * den["excluded_value_ars"] / den["total_portfolio_value_ars"])
    assert cov["out_of_scope_pct_of_total_portfolio"] == pytest.approx(100 * den["out_of_scope_value_ars"] / den["total_portfolio_value_ars"])
    assert cov["covered_pct_of_scope"] == pytest.approx(100 * den["included_value_ars"] / den["scope_value_ars"])
    assert set(den["definitions"]) >= {"covered_pct_of_total_portfolio", "covered_pct_of_scope", "weight_scope_pct"}
    # meta.scope_value_ars must be the equity scope, not the ALL-portfolio value the endpoint reports
    assert payload["meta"]["scope_value_ars"] == 900_000.0
    assert payload["meta"]["total_value_ars"] == TOTAL


def test_an_excluded_short_history_symbol_does_not_create_or_explain_bridged_returns(monkeypatch):
    def history(kind):
        series = _series(kind)
        if kind == "CEDEARS":  # DDD: short history, excluded; its bars sit on dates AAA also has
            series["DDD"]["observations"] = series["DDD"]["observations"][-12:]
            del series["DDD"]["observations"][5]  # an interior hole in an EXCLUDED symbol
        return series

    _install(monkeypatch, history=history)
    payload = _run(lookback_sessions=40)
    assert payload["status"] == "ok"
    cov = payload["meta"]["coverage"]
    assert [r["symbol"] for r in cov["excluded_from_statistics"]] == ["DDD"]
    assert cov["bridged_returns"] == 0 and cov["bridged_return_details"] == []
