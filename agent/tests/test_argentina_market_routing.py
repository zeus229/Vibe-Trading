"""Regression tests for Yahoo-backed Argentina (.BA) market routing.

These tests are deterministic and do not make network requests.  They pin the
routing contract separately from Yahoo data availability.
"""

from __future__ import annotations

import pytest

from backtest.engines._market_hooks import _detect_market, code_currency
from backtest.loaders.registry import FALLBACK_CHAINS
from backtest.loaders.yahoo_loader import _is_supported as yahoo_is_supported
from backtest.loaders.yfinance_loader import _to_yfinance_symbol
from backtest.runner import _create_market_engine, _detect_source
from src.market_data import detect_source
from src.tools.stock_profile_tool import _market_for


@pytest.mark.parametrize("symbol", ["GGAL.BA", "PAMP.BA", "GOOGL.BA"])
def test_ba_symbols_share_one_market_data_contract(symbol: str) -> None:
    assert _detect_market(symbol) == "ar_equity"
    assert code_currency(symbol) == "ARS"
    assert detect_source(symbol) == "yahoo"
    assert _detect_source(symbol) == "yahoo"
    assert yahoo_is_supported(symbol)
    assert _to_yfinance_symbol(symbol) == symbol
    assert _market_for(symbol) == "ar"


def test_argentina_fallback_chain_is_yahoo_first() -> None:
    assert FALLBACK_CHAINS["ar_equity"] == ["yahoo", "yfinance", "local"]


def test_argentina_backtest_routes_to_byma_engine() -> None:
    from datetime import date

    from backtest.engines.argentina_equity import ArgentinaEquityEngine

    config = {
        "ar_instruments": {
            "GGAL.BA": {"kind": "share", "currency": "ARS", "lot_size": 1, "tick_size": "0.05"},
        },
        "ar_settlement_sessions": ["2024-05-27", "2024-05-28"],
    }
    engine = _create_market_engine("yahoo", config, ["GGAL.BA"])
    assert isinstance(engine, ArgentinaEquityEngine)
    assert engine.settlement_sessions == (date(2024, 5, 27), date(2024, 5, 28))


def test_argentina_backtest_without_byma_metadata_still_fails_closed() -> None:
    with pytest.raises(ValueError, match="ar_instruments must declare BYMA instrument metadata"):
        _create_market_engine("yahoo", {}, ["GGAL.BA"])
