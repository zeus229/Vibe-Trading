"""Focused regressions for dynamic Asistente Casa Risk X-Ray session windows."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import pytest

from src.tools import asistente_casa_portfolio_risk_tool as risk_module
from src.tools.asistente_casa_portfolio_risk_tool import AsistenteCasaPortfolioRiskXrayTool


def _observations(periods: int, *, start: str = "2026-06-01") -> list[dict[str, object]]:
    dates = pd.bdate_range(start=start, periods=periods)
    return [
        {"date": ts.date().isoformat(), "close": 8000.0 + i * 10.0}
        for i, ts in enumerate(dates)
    ]


def _portfolio_payload() -> dict[str, object]:
    return {
        "ok": True,
        "contract_version": "test-v1",
        "position_quantity_source": "test",
        "position_snapshot_id": "snap-1",
        "position_snapshot_at": "2026-09-23T12:00:00Z",
        "portfolio": {
            "positions": [
                {
                    "symbol": "YPFD",
                    "name": "YPF",
                    "weight_scope": 1.0,
                }
            ],
            "total_value_ars": 1000000.0,
            "scope_value_ars": 1000000.0,
        },
    }


def _history_payload(observations: list[dict[str, object]]) -> dict[str, object]:
    return {
        "ok": True,
        "numeric_format": "json_number",
        "policy": "persisted_only",
        "interval": "1D",
        "complete": True,
        "unresolved_symbols": [],
        "unsafe_symbols": [],
        "symbols_without_history": [],
        "contract_version": "history-test-v1",
        "source_selection": "canonical",
        "series": {"YPFD": {"observations": observations}},
    }


@pytest.mark.parametrize("sessions", [21, 42])
def test_dynamic_lookback_sessions_selects_exact_shared_tail(
    monkeypatch: pytest.MonkeyPatch,
    sessions: int,
) -> None:
    monkeypatch.setenv("ASISTENTE_CASA_BASE_URL", "http://ac.local")
    monkeypatch.setenv("ASISTENTE_CASA_API_KEY", "secret")
    observations = _observations(70)

    def fake_get(base_url, api_key, path, params):
        assert base_url == "http://ac.local"
        assert api_key == "secret"
        if path == "/inversiones/vibe/portfolio":
            return _portfolio_payload()
        if path == "/inversiones/vibe/market-history/coverage":
            return {
                "ok": True,
                "numeric_format": "json_number",
                "policy": "persisted_only",
                "earliest_common_date": observations[0]["date"],
            }
        if path == "/inversiones/vibe/market-history":
            return _history_payload(observations)
        raise AssertionError(path)

    monkeypatch.setattr(risk_module, "_get", fake_get)

    payload = json.loads(
        AsistenteCasaPortfolioRiskXrayTool().execute(
            asset_type="ACCIONES",
            lookback_sessions=sessions,
            end_date="2026-09-23",
        )
    )

    assert payload["status"] == "ok"
    assert payload["meta"]["horizon"] == "lookback_sessions"
    assert payload["meta"]["lookback_sessions"] == sessions
    assert payload["meta"]["common_date_count"] == sessions
    assert payload["data"]["inputs"]["aligned_days"] == sessions
    assert payload["data"]["inputs"]["return_observations"] == sessions - 1
    assert payload["meta"]["start_date"] == observations[-sessions]["date"]
    assert payload["meta"]["end_date"] == observations[-1]["date"]
    assert payload["data"]["concentration"]["top1_weight"] == pytest.approx(1.0)

    warnings = payload["data"]["warnings"]
    if sessions < risk_module.MIN_HISTORY_DAYS:
        assert any("short-window risk estimates may be less stable" in item for item in warnings)
    else:
        assert not any("short-window risk estimates may be less stable" in item for item in warnings)


def test_lookback_sessions_is_mutually_exclusive_with_horizon_and_start_date() -> None:
    tool = AsistenteCasaPortfolioRiskXrayTool()

    with_horizon = json.loads(
        tool.execute(
            asset_type="ACCIONES",
            lookback_sessions=21,
            horizon="YTD",
        )
    )
    assert with_horizon["status"] == "error"
    assert "mutually exclusive" in with_horizon["error"]

    with_start = json.loads(
        tool.execute(
            asset_type="ACCIONES",
            lookback_sessions=21,
            start_date="2026-08-01",
        )
    )
    assert with_start["status"] == "error"
    assert "mutually exclusive" in with_start["error"]


@pytest.mark.parametrize("bad_value", [True, 1, 3.5, "21"])
def test_lookback_sessions_rejects_invalid_values(bad_value) -> None:
    payload = json.loads(
        AsistenteCasaPortfolioRiskXrayTool().execute(
            asset_type="ACCIONES",
            lookback_sessions=bad_value,
        )
    )
    assert payload["status"] == "error"


def test_lookback_sessions_widens_once_to_coverage_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASISTENTE_CASA_BASE_URL", "http://ac.local")
    monkeypatch.setenv("ASISTENTE_CASA_API_KEY", "secret")
    full = _observations(80, start="2025-01-02")
    recent = full[-20:]
    history_calls: list[str] = []

    def fake_get(base_url, api_key, path, params):
        if path == "/inversiones/vibe/portfolio":
            return _portfolio_payload()
        if path == "/inversiones/vibe/market-history/coverage":
            return {
                "ok": True,
                "numeric_format": "json_number",
                "policy": "persisted_only",
                "earliest_common_date": full[0]["date"],
            }
        if path == "/inversiones/vibe/market-history":
            history_calls.append(params["from"])
            if params["from"] == full[0]["date"]:
                return _history_payload(full)
            return _history_payload(recent)
        raise AssertionError(path)

    monkeypatch.setattr(risk_module, "_get", fake_get)

    payload = json.loads(
        AsistenteCasaPortfolioRiskXrayTool().execute(
            asset_type="ACCIONES",
            lookback_sessions=42,
            end_date="2026-09-23",
        )
    )

    assert payload["status"] == "ok"
    assert len(history_calls) == 2
    assert history_calls[-1] == full[0]["date"]
    assert payload["meta"]["common_date_count"] == 42
    assert payload["data"]["inputs"]["aligned_days"] == 42


def test_lookback_sessions_fails_closed_when_coverage_is_insufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ASISTENTE_CASA_BASE_URL", "http://ac.local")
    monkeypatch.setenv("ASISTENTE_CASA_API_KEY", "secret")
    observations = _observations(10, start="2026-09-01")

    def fake_get(base_url, api_key, path, params):
        if path == "/inversiones/vibe/portfolio":
            return _portfolio_payload()
        if path == "/inversiones/vibe/market-history/coverage":
            return {
                "ok": True,
                "numeric_format": "json_number",
                "policy": "persisted_only",
                "earliest_common_date": observations[0]["date"],
            }
        if path == "/inversiones/vibe/market-history":
            return _history_payload(observations)
        raise AssertionError(path)

    monkeypatch.setattr(risk_module, "_get", fake_get)

    payload = json.loads(
        AsistenteCasaPortfolioRiskXrayTool().execute(
            asset_type="ACCIONES",
            lookback_sessions=21,
            end_date="2026-09-23",
        )
    )

    assert payload["status"] == "error"
    assert "requested 21 shared trading sessions but only 10 are available" in payload["error"]
