from __future__ import annotations

import pytest

from src.portfolio.config import PortfolioSettingsStore
from src.portfolio import config as portfolio_config
from src.portfolio import service as portfolio_service
from src.portfolio.service import PortfolioService
from src.portfolio.store import PortfolioStore
from src.trading import connections as trading_connections
from src.trading.types import TradingProfile


def test_native_account_separates_unsettled_cash(tmp_path, monkeypatch):
    profile = TradingProfile(
        id="asistente-casa-live-readonly",
        connector="asistente-casa",
        label="Asistente Casa",
        environment="live",
        transport="local_plugin",
        capabilities=("account.read", "positions.read"),
        readonly=True,
    )
    def _profile_by_id(profile_id):
        if profile_id == profile.id:
            return profile
        raise ValueError(profile_id)

    # PortfolioService and ConnectionStore import the resolver into their own
    # modules, so patch both call sites used by this isolated local-plugin fixture.
    monkeypatch.setattr(portfolio_service, "profile_by_id", _profile_by_id)
    monkeypatch.setattr(trading_connections, "profile_by_id", _profile_by_id)
    monkeypatch.setattr(portfolio_config, "profile_by_id", _profile_by_id)
    monkeypatch.setattr(portfolio_config, "list_profiles", lambda: [profile])

    settings = PortfolioSettingsStore(tmp_path / "portfolio.json")
    settings.connection_store.ensure("ars", profile.id, "ARS")
    settings.save({
        "display_currency": "ARS",
        "sources": [{
            "connection_id": "ars",
            "label": "ARS",
            "enabled": True,
            "order": 0,
            "include_cash": True,
        }],
    })
    def _get_account(profile_id, *, connection_id=None):
        assert profile_id == profile.id
        assert connection_id == "ars"
        return {"account": {
            "portfolio_value": "1000",
            "cash": "100",
            "unsettled_cash": "200",
            "currency": "ARS",
        }}

    def _get_positions(profile_id, *, connection_id=None):
        assert profile_id == profile.id
        assert connection_id == "ars"
        return {"positions": [{
            "symbol": "GGAL",
            "quantity": "1",
            "average_cost": "600",
            "current_price": "700",
            "market_value": "700",
            "currency": "ARS",
        }]}

    def _unexpected_fx():
        raise AssertionError("native ARS portfolio must not request FX")

    service = PortfolioService(
        PortfolioStore(tmp_path / "portfolio.sqlite3"),
        settings_store=settings,
        get_account=_get_account,
        get_positions=_get_positions,
        get_quote=lambda *args, **kwargs: {},
        fx_fetcher=_unexpected_fx,
    )

    snapshot = service.refresh()
    account = snapshot["accounts"][0]
    native = snapshot["valuation"]["native_by_currency"]["ARS"]
    allocation = service.analysis_context()["account_allocation"][0]

    assert snapshot["display_currency"] == "ARS"
    assert snapshot["totals"]["native_by_currency"]["ARS"] == 1000.0
    assert account["status"] == "ok"
    assert account["broker"] == "asistente-casa"
    assert account["native_currency"] == "ARS"
    assert account["total_native"] == 1000.0
    assert account["priced_value_native"] == 700.0
    assert account["cash_native"] == 100.0
    assert account["unsettled_cash_native"] == 200.0
    assert account["unpriced_or_other_native"] == 0.0
    assert native["priced"] == 700.0
    assert native["cash"] == 100.0
    assert native["unsettled_cash"] == 200.0
    assert native["unpriced_or_other"] == 0.0
    assert allocation["unsettled_cash_native"] == 200.0


def _refresh_with_account(tmp_path, monkeypatch, account):
    profile = TradingProfile(
        id="asistente-casa-live-readonly", connector="asistente-casa", label="Asistente Casa",
        environment="live", transport="local_plugin",
        capabilities=("account.read", "positions.read"), readonly=True,
    )
    resolver = lambda profile_id: profile if profile_id == profile.id else (_ for _ in ()).throw(ValueError(profile_id))
    monkeypatch.setattr(portfolio_service, "profile_by_id", resolver)
    monkeypatch.setattr(trading_connections, "profile_by_id", resolver)
    monkeypatch.setattr(portfolio_config, "profile_by_id", resolver)
    monkeypatch.setattr(portfolio_config, "list_profiles", lambda: [profile])
    settings = PortfolioSettingsStore(tmp_path / "portfolio.json")
    settings.connection_store.ensure("ars", profile.id, "ARS")
    settings.save({"display_currency": "ARS", "sources": [{
        "connection_id": "ars", "label": "ARS", "enabled": True, "order": 0, "include_cash": True}]})
    service = PortfolioService(
        PortfolioStore(tmp_path / "portfolio.sqlite3"), settings_store=settings,
        get_account=lambda profile_id, *, connection_id=None: {"account": account},
        get_positions=lambda profile_id, *, connection_id=None: {"positions": [{
            "symbol": "GGAL", "quantity": "1", "average_cost": "600", "current_price": "700",
            "market_value": "700", "currency": "ARS"}]},
        get_quote=lambda *a, **k: {},
        fx_fetcher=lambda: (_ for _ in ()).throw(AssertionError("no FX")),
    )
    return service, service.refresh()


def test_cash_semantics_survive_negative_pending_folded_into_cash(tmp_path, monkeypatch):
    # settled 500, pending -200 -> the connector folds economic cash (300) into ``cash``.
    service, snapshot = _refresh_with_account(tmp_path, monkeypatch, {
        "portfolio_value": "1000", "cash": "300", "unsettled_cash": "0", "currency": "ARS",
        "cash_semantics": {
            "settled_cash_ars": 500.0, "unsettled_signed_ars": -200.0, "economic_cash_ars": 300.0,
            "projection": "negative_pending_folded_into_cash", "cash_field_meaning": "economic",
            "secret_extra": "dropped",
        },
    })
    account = snapshot["accounts"][0]
    assert account["cash_native"] == 300.0 and account["unsettled_cash_native"] == 0.0
    assert account["cash_semantics"]["settled_cash_ars"] == 500.0
    assert account["cash_semantics"]["unsettled_signed_ars"] == -200.0
    assert account["cash_semantics"]["economic_cash_ars"] == 300.0
    assert "secret_extra" not in account["cash_semantics"]
    allocation = service.analysis_context()["account_allocation"][0]
    assert allocation["cash_semantics"]["unsettled_signed_ars"] == -200.0


@pytest.mark.parametrize("bad", [
    None,
    {"settled_cash_ars": 1.0},
    {"settled_cash_ars": "x", "unsettled_signed_ars": 0, "economic_cash_ars": 0},
    {"settled_cash_ars": float("nan"), "unsettled_signed_ars": 0, "economic_cash_ars": 0},
])
def test_cash_semantics_absent_or_malformed_is_none(tmp_path, monkeypatch, bad):
    account = {"portfolio_value": "1000", "cash": "300", "unsettled_cash": "0", "currency": "ARS"}
    if bad is not None:
        account["cash_semantics"] = bad
    _, snapshot = _refresh_with_account(tmp_path, monkeypatch, account)
    assert snapshot["accounts"][0]["cash_semantics"] is None


def test_native_unrealized_pnl_reaches_agent_context(tmp_path, monkeypatch):
    service, _ = _refresh_with_account(tmp_path, monkeypatch, {
        "portfolio_value": "1000", "cash": "300", "unsettled_cash": "0", "currency": "ARS",
    })
    context = service.analysis_context()
    row = context["holdings_native"]["ARS"][0]
    # cost 600 x 1 vs price 700 -> +100 ARS, derived natively (no FX, no USD).
    assert row["unrealized_pnl_native"] == 100.0
    assert context["unrealized_pnl_native_by_currency"]["ARS"] == {
        "unrealized_pnl_native": 100.0, "positions_with_pnl": 1, "positions_total": 1,
    }
