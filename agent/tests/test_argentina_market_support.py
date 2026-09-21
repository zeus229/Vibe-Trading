"""Deterministic Argentina/BYMA Phase 1A regression tests."""

import json
from unittest.mock import patch

from backtest.benchmark import _infer_market as infer_benchmark_market
from backtest.correlation import infer_market as infer_correlation_market
from backtest.engines._market_hooks import _detect_market, _detect_submarket, code_currency
from backtest.loaders.registry import FALLBACK_CHAINS
from backtest.loaders.yahoo_loader import _is_supported as yahoo_is_supported
from backtest.loaders.yfinance_loader import DataLoader as YFinanceLoader
from src.market_data import detect_source
from src.shadow_account.extractor import _MARKET_KEY_MAP
from src.tools.financial_statements_tool import FinancialStatementsTool, _classify_market
from src.tools.stock_profile_tool import _market_for
from src.tools.symbol_search_tool import _is_argentina_symbol
from src.tools.trade_journal_parsers import _infer_market_from_symbol

ARGENTINA_EQUITIES = ("PAMP.BA", "YPFD.BA", "GGAL.BA", "ALUA.BA")
NON_EQUITY_CONTROLS = ("AL30", "GD30", "FCI_TEST")


def test_argentina_equity_detection_and_currency() -> None:
    for ticker in ARGENTINA_EQUITIES:
        assert _detect_market(ticker) == "ar_equity"
        assert code_currency(ticker) == "ARS"
        assert _detect_submarket([ticker]) == "ar"


def test_argentina_routes_to_yahoo_and_backtest_fallbacks() -> None:
    assert FALLBACK_CHAINS["ar_equity"] == ["yahoo", "yfinance", "local"]
    assert "ar_equity" in YFinanceLoader.markets
    for ticker in ARGENTINA_EQUITIES:
        assert detect_source(ticker) == "yahoo"
        assert yahoo_is_supported(ticker)
        assert infer_correlation_market(ticker) == "ar_equity"
        assert infer_benchmark_market([ticker], "yahoo") == "ar_equity"


def test_argentina_tool_classification() -> None:
    for ticker in ARGENTINA_EQUITIES:
        assert _classify_market(ticker) == "ar"
        assert _market_for(ticker) == "ar"
        assert _infer_market_from_symbol(ticker) == "ar"
        assert _is_argentina_symbol(ticker)


def test_argentina_financial_statements_use_yahoo_without_network() -> None:
    fake = {"periods": [{"endDate": 1, "totalRevenue": 100}]}
    with patch(
        "src.tools.financial_statements_tool._fetch_yahoo_statement",
        return_value=fake,
    ) as fetch:
        payload = json.loads(
            FinancialStatementsTool().execute(
                code="GGAL.BA", statement="income", period="annual"
            )
        )
    assert payload["ok"] is True
    assert payload["market"] == "ar"
    assert payload["source"] == "yahoo"
    fetch.assert_called_once()


def test_bonds_and_fci_do_not_become_argentina_equities() -> None:
    for symbol in NON_EQUITY_CONTROLS:
        assert _detect_market(symbol) != "ar_equity"
        assert _classify_market(symbol) is None
        assert _infer_market_from_symbol(symbol) != "ar"
        assert not _is_argentina_symbol(symbol)


def test_shadow_account_maps_argentina_journal_market() -> None:
    assert _MARKET_KEY_MAP["ar"] == "ar_equity"
