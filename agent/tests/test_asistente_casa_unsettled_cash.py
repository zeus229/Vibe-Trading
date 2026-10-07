from __future__ import annotations

from decimal import Decimal

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
    service = PortfolioService(
        PortfolioStore(tmp_path / "portfolio.sqlite3"),
        settings_store=settings,
        get_account=lambda profile_id: {"account": {
            "portfolio_value": "1000",
            "cash": "100",
            "unsettled_cash": "200",
            "currency": "ARS",
        }},
        get_positions=lambda profile_id: {"positions": [{
            "symbol": "GGAL",
            "quantity": "1",
            "average_cost": "600",
            "current_price": "700",
            "market_value": "700",
            "currency": "ARS",
        }]},
        get_quote=lambda *args, **kwargs: {},
        fx_fetcher=lambda: (
            Decimal("7.2"),
            Decimal("7.8"),
            "2026-10-06T00:00:00+00:00",
        ),
    )

    snapshot = service.refresh()
    account = snapshot["accounts"][0]
    native = snapshot["valuation"]["native_by_currency"]["ARS"]
    allocation = service.analysis_context()["account_allocation"][0]

    assert snapshot["totals"]["native_by_currency"]["ARS"] == 1000.0
    assert account["cash_native"] == 100.0
    assert account["unsettled_cash_native"] == 200.0
    assert account["unpriced_or_other_native"] == 0.0
    assert native["priced"] == 700.0
    assert native["cash"] == 100.0
    assert native["unsettled_cash"] == 200.0
    assert native["unpriced_or_other"] == 0.0
    assert allocation["unsettled_cash_native"] == 200.0
