"""Regression gates for Asistente Casa ISIN-first provider identity in Vibe."""

from __future__ import annotations

import json
from decimal import Decimal

from src.agent.grounding import GroundingLedger
from src.market_data import detect_source
from src.portfolio.normalization import normalize_position, value_position


def _verified_identity(symbol: str, isin: str) -> dict:
    return {
        "provider": "yahoo",
        "symbol": symbol,
        "resolution": "isin",
        "verified": True,
        "resolved_by_isin": isin,
        "persisted_mapping": {"symbol": symbol, "agrees": True},
        "warnings": [],
        "canonical_history": {
            "endpoint": "/inversiones/vibe/market-history",
            "symbol": symbol.removesuffix(".BA"),
            "instrument_id": f"accion:{symbol.removesuffix('.BA')}",
        },
    }


def _portfolio_result(identity: dict, *, isin: str = "ARP495251018") -> str:
    return json.dumps(
        {
            "status": "ok",
            "context": {
                "holdings_native": {
                    "ARS": [
                        {
                            "source_instrument_id": "accion:GGAL",
                            "source_instrument_type": "ACCIONES",
                            "symbol": "GGAL",
                            "isin": isin,
                            "market": "BYMA",
                            "provider_identity": identity,
                            "underlying": None,
                            "market_value_native": 1000.0,
                            "native_currency": "ARS",
                        }
                    ]
                }
            },
        }
    )


def test_asistente_casa_provider_identity_survives_normalization_and_valuation():
    identity = _verified_identity("GGAL.BA", "ARP495251018")
    raw = {
        "symbol": "GGAL",
        "name": "Grupo Financiero Galicia",
        "asset_type": "stock",
        "quantity": 10,
        "market_price": 6685,
        "price_currency": "ARS",
        "currency": "ARS",
        "market_value": 66850,
        "source": "asistente_casa",
        "source_instrument_id": "accion:GGAL",
        "source_instrument_type": "ACCIONES",
        "isin": "ARP495251018",
        "market": "BYMA",
        "provider_identity": identity,
        "underlying": None,
    }
    normalized = normalize_position("asistente-casa", raw)
    valued = value_position(
        normalized,
        usd_hkd=Decimal("0"),
        usd_cny=Decimal("0"),
        native_currency="ARS",
    )
    assert valued["symbol"] == "GGAL"
    assert valued["isin"] == "ARP495251018"
    assert valued["provider_identity"] == identity
    assert valued["provider_identity"]["symbol"] == "GGAL.BA"


def test_non_asistente_connector_cannot_smuggle_trusted_provider_identity():
    raw = {
        "symbol": "AAPL",
        "quantity": 1,
        "market_price": 100,
        "currency": "USD",
        "provider_identity": _verified_identity("GGAL.BA", "ARP495251018"),
    }
    normalized = normalize_position("fake-broker", raw)
    assert normalized["provider_identity"] is None
    assert normalized["underlying"] is None


def test_verified_isin_portfolio_identity_locks_ba_symbol(tmp_path):
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analiza mi cartera")
    identity = _verified_identity("GGAL.BA", "ARP495251018")
    ledger.ingest_tool_result(
        tool_name="portfolio_summary",
        arguments={},
        result=_portfolio_result(identity),
        call_id="call-portfolio",
        success=True,
    )

    assert ledger.identity_status == "locked"
    assert "GGAL.BA" in ledger.authorized_symbols
    record = next(
        row for row in ledger.identity_summary()["records"]
        if row["symbol"] == "GGAL.BA"
    )
    assert record["venue"] == "buenos_aires"
    assert record["currency"] == "ARS"
    assert record["source"] == ["asistente-casa:provider_identity"]

    authorization = ledger.authorize_tool_call(
        "technical_indicators",
        {"symbol": "GGAL.BA"},
        batch_authorized_symbols=ledger.authorized_symbols,
        batch_identity_status=ledger.identity_status,
        call_id="call-tech",
    )
    assert authorization.allowed is True


def test_unverified_or_wrong_isin_portfolio_identity_does_not_lock(tmp_path):
    cases = [
        {
            **_verified_identity("GGAL.BA", "ARP495251018"),
            "verified": False,
            "resolution": "persisted_mapping",
            "resolved_by_isin": None,
        },
        {
            **_verified_identity("GGAL.BA", "ARP495251018"),
            "resolved_by_isin": "ARP9897X1319",
        },
        {
            **_verified_identity("GGAL.US", "ARP495251018"),
            "symbol": "GGAL.US",
        },
    ]
    for index, identity in enumerate(cases):
        run_dir = tmp_path / str(index)
        ledger = GroundingLedger(run_dir=run_dir, user_message="Analiza mi cartera")
        ledger.ingest_tool_result(
            tool_name="portfolio_summary",
            arguments={},
            result=_portfolio_result(identity),
            call_id=f"call-{index}",
            success=True,
        )
        assert "GGAL.BA" not in ledger.authorized_symbols
        assert "GGAL.US" not in ledger.authorized_symbols


def test_ba_symbol_routes_to_yahoo_in_auto_mode():
    assert detect_source("GGAL.BA") == "yahoo"
    assert detect_source("SPY.BA") == "yahoo"
