"""Window widening in the joint ACCIONES+CEDEARS Risk X-Ray (PR #96).

A usable (even partial) panel must never trigger a wider history fetch: a wider
range cannot add history to short-history symbols but would cross corporate-action
events that Asistente Casa's identity guard refuses by design. Widening happens only
when there is no usable panel at all, and is bounded to 3*N+30 calendar days.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tools import asistente_casa_portfolio_risk_tool as risk_module
from src.tools.asistente_casa_portfolio_risk_tool import AsistenteCasaPortfolioRiskXrayTool

END = date(2026, 4, 30)
TOTAL = 1_000_000.0
HOLDINGS = {
    "AAA": ("ACCIONES", 400_000.0),
    "BBB": ("ACCIONES", 200_000.0),
    "CCC": ("CEDEARS", 300_000.0),
    "NEWC": ("CEDEARS", 50_000.0),  # recent holding, short history (NU / PFE case)
    "BOND": ("BONOS", 50_000.0),
}
EVENT_DATE = "2025-06-15"  # CEDEAR ratio change: ranges crossing it are unsafe in AC
ALL_DATES = [d.date().isoformat() for d in pd.bdate_range("2025-06-02", "2026-04-30")]


def _closes(symbol: str, dates: list[str]) -> list[float]:
    rng = np.random.default_rng(abs(hash(symbol)) % 1000 + 1)
    return list(1000.0 * np.cumprod(1 + rng.normal(0.0004, 0.015, len(dates))))


def _install(monkeypatch, *, every=1, short_symbols=None, currency="ARS"):
    """Fake AC. ``every`` keeps one bar out of N (sparse data); the guard mirrors AC's."""
    monkeypatch.setenv("ASISTENTE_CASA_BASE_URL", "http://ac.local")
    monkeypatch.setenv("ASISTENTE_CASA_API_KEY", "secret")
    short_symbols = short_symbols or {}
    calls: list[tuple[str, dict]] = []
    base = ALL_DATES[::every]

    def fake_get(base_url, api_key, path, params):
        calls.append((path, dict(params)))
        if path == "/inversiones/vibe/portfolio":
            return {
                "ok": True, "contract_version": "t", "position_snapshot_id": "snap",
                "portfolio": {
                    "positions": [
                        {"symbol": s, "name": s, "instrument_type": k, "market_value_ars": v,
                         "weight_total_portfolio": v / TOTAL, "weight_scope": 0.0}
                        for s, (k, v) in HOLDINGS.items()
                    ],
                    "total_value_ars": TOTAL, "scope_value_ars": TOTAL,
                },
            }
        if path == "/inversiones/vibe/market-history/coverage":
            return {"ok": True, "numeric_format": "json_number", "policy": "persisted_only",
                    "earliest_any_date": "2023-10-10", "earliest_common_date": "2026-04-20"}
        if path == "/inversiones/vibe/market-history":
            kind, lo, hi = params["asset_type"], params["from"], params["to"]
            symbols = [s for s in params["symbols"].split(",") if s]
            unsafe = []
            if kind == "CEDEARS" and lo <= EVENT_DATE <= hi:
                unsafe = [{"symbol": "CCC", "instrument_id": "cedear:CCC",
                           "reason": "corporate_action_adjustment_required",
                           "event": {"action_date": EVENT_DATE, "action_type": "cedear_ratio_change"}}]
            series = {}
            for s in symbols:
                dates = [d for d in base if lo <= d <= hi]
                if s in short_symbols:
                    dates = dates[-short_symbols[s]:]
                closes = dict(zip(base, _closes(s, base)))
                series[s] = {"currency": currency, "observations": [
                    {"date": d, "close": closes[d], "currency": currency, "source": "ppi",
                     "quality": "valid"} for d in dates]}
            return {"ok": True, "numeric_format": "json_number", "policy": "persisted_only",
                    "interval": "1D", "complete": True, "unresolved_symbols": [],
                    "unsafe_symbols": unsafe, "contract_version": "h", "series": series}
        raise AssertionError(path)

    monkeypatch.setattr(risk_module, "_get", fake_get)
    return calls


def _run(n):
    return json.loads(AsistenteCasaPortfolioRiskXrayTool().execute(
        asset_type="ACCIONES+CEDEARS", end_date=END.isoformat(), lookback_sessions=n))


def _history_calls(calls):
    return [c[1] for c in calls if c[0] == "/inversiones/vibe/market-history"]


def test_usable_partial_panel_is_not_widened(monkeypatch):
    calls = _install(monkeypatch, short_symbols={"NEWC": 5})
    payload = _run(126)
    assert payload["status"] == "ok"
    assert payload["meta"]["coverage_mode"] == "partial_high_coverage"
    cov = payload["meta"]["coverage"]
    assert [r["symbol"] for r in cov["excluded_from_statistics"]] == ["NEWC"]
    assert {r["symbol"] for r in cov["included"]} == {"AAA", "BBB", "CCC"}
    hist = _history_calls(calls)
    # exactly one fetch per instrument type, all from the initial (2N+14 days) start
    assert len(hist) == 2
    initial = (END - timedelta(days=max(120, 126 * 2 + 14))).isoformat()
    assert {h["from"] for h in hist} == {initial}


def test_full_basket_window_is_a_single_fetch(monkeypatch):
    calls = _install(monkeypatch)
    payload = _run(63)
    assert payload["status"] == "ok" and payload["meta"]["coverage_mode"] == "strict_full_basket"
    assert payload["meta"]["coverage"]["return_observations"] == 63
    assert len(_history_calls(calls)) == 2


def test_widening_only_without_usable_panel_and_bounded(monkeypatch):
    calls = _install(monkeypatch, every=2)  # sparse data: first window lacks N+1 closes
    payload = _run(60)
    assert payload["status"] == "ok"
    hist = _history_calls(calls)
    assert len(hist) == 4  # initial fetch + exactly one widened fetch, per type
    widened = {h["from"] for h in hist[2:]}
    assert widened == {(END - timedelta(days=3 * 60 + 30)).isoformat()}
    assert payload["meta"]["coverage"]["return_observations"] == 60


def test_widening_never_goes_past_the_bound_into_coverage_start(monkeypatch):
    calls = _install(monkeypatch, every=2)
    _run(60)
    assert all(h["from"] >= (END - timedelta(days=3 * 60 + 30)).isoformat() for h in _history_calls(calls))


def test_unsafe_corporate_action_inside_the_window_fails_explicitly(monkeypatch):
    # N=100 sparse: the bounded widening (330 days) reaches 2025-06-04 and crosses EVENT_DATE
    _install(monkeypatch, every=2)
    payload = _run(100)
    assert payload["status"] == "error"
    assert "corporate_action_adjustment_required" in payload["error"]
    assert "unresolved/unsafe identities" in payload["error"]


def test_guard_is_not_swallowed_on_a_legitimate_long_window(monkeypatch):
    # an explicit very long window that legitimately crosses the event must fail
    _install(monkeypatch)
    payload = _run(252)
    assert payload["status"] == "error"
    assert "corporate_action_adjustment_required" in payload["error"]


def test_insufficient_data_is_an_explicit_error_never_fabricated_metrics(monkeypatch):
    _install(monkeypatch, short_symbols={"AAA": 4, "BBB": 4, "CCC": 4, "NEWC": 4})
    payload = _run(60)
    assert payload["status"] == "error"
    assert "data" not in payload or not payload.get("data")
    assert "shared closes" in payload["error"] or "insufficient" in payload["error"].lower()


def test_non_ars_series_is_still_refused_after_the_change(monkeypatch):
    _install(monkeypatch, currency="USD")
    payload = _run(63)
    assert payload["status"] == "error"
    assert "ARS" in payload["error"]


def test_multi_session_return_warning_is_preserved(monkeypatch):
    _install(monkeypatch)
    real = risk_module._get

    def drop_a_bar(base_url, api_key, path, params):
        out = real(base_url, api_key, path, params)
        if path == "/inversiones/vibe/market-history" and params["asset_type"] == "ACCIONES":
            obs = out["series"]["AAA"]["observations"]
            del obs[len(obs) // 2]
        return out

    monkeypatch.setattr(risk_module, "_get", drop_a_bar)
    payload = _run(63)
    assert payload["status"] == "ok"
    assert payload["meta"]["coverage"]["bridged_returns"] >= 1
    assert any("span more than one session" in w for w in payload["data"]["warnings"])


def test_adapter_is_read_only_and_cannot_enable_trading():
    src = Path(risk_module.__file__).read_text()
    assert "PPI_TRADING_DISABLED" not in src
    assert "/orders" not in src and "place_order" not in src
    assert "requests.post" not in src.lower()
