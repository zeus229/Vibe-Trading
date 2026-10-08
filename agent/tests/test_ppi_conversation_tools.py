"""Focused isolation and calculation contracts for Asistente Casa PPI research."""
from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from src.config.schema import AgentConfig, MCPServerConfig
from src.tools.ppi_conversation_scope import (
    allow_ppi_conversation_tools,
    scope_asistente_casa_agent_config,
)
from src.tools.ppi_indicators_tool import CalculatePPIIndicatorsTool


def _config(allowed=None):
    return AgentConfig(mcp_servers={
        "asistente-casa": MCPServerConfig(
            type="streamableHttp",
            url="https://example.test/mcp",
            enabled_tools=allowed or [
                "consultar_performance_cartera_scope",
                "consultar_attribution_cartera_scope",
            ],
        )
    })


def test_only_authenticated_conversation_gets_ppi_reads():
    assert allow_ppi_conversation_tools(SimpleNamespace(owner=object(), title="mi cartera"))
    assert not allow_ppi_conversation_tools(SimpleNamespace(owner=None, title="mi cartera"))
    assert not allow_ppi_conversation_tools(SimpleNamespace(owner=object(), title="scheduled-research:123"))
    assert not allow_ppi_conversation_tools(None)


def test_scoped_tools_do_not_mutate_operator_config():
    original = _config()
    conversation = scope_asistente_casa_agent_config(original, conversational=True)
    scheduled = scope_asistente_casa_agent_config(original, conversational=False)
    enabled = conversation.mcp_servers["asistente-casa"].enabled_tools
    assert "consultar_cartera_actual" in enabled
    assert "consultar_mercado_ppi_instrumento" in enabled
    assert "consultar_performance_cartera_scope" in enabled
    assert scheduled.mcp_servers["asistente-casa"].enabled_tools == original.mcp_servers["asistente-casa"].enabled_tools
    assert len(original.mcp_servers["asistente-casa"].enabled_tools) == 2


def test_scheduled_fail_closed_even_if_global_allowlist_expanded():
    config = _config(["consultar_performance_cartera_scope", "consultar_cartera_actual",
                      "consultar_mercado_ppi_instrumento"])
    scheduled = scope_asistente_casa_agent_config(config, conversational=False)
    assert scheduled.mcp_servers["asistente-casa"].enabled_tools == [
        "consultar_performance_cartera_scope"
    ]


def test_wildcard_never_exposes_all_ppi_tools_to_scheduler():
    config = _config(["*"])
    scheduled = scope_asistente_casa_agent_config(config, conversational=False)
    assert "*" not in scheduled.mcp_servers["asistente-casa"].enabled_tools
    assert "consultar_mercado_ppi_instrumento" not in scheduled.mcp_servers["asistente-casa"].enabled_tools


def _payload(n=83):
    first = date(2026, 6, 10)
    bars = [{
        "date": (first + timedelta(days=i)).isoformat(),
        "open": 99 + i, "high": 101 + i, "low": 98 + i,
        "close": 100 + i, "volume": 1000 + i,
    } for i in range(n)]
    return {
        "ok": True, "contract_version": "asistente_casa_ppi_market_data_v1",
        "source": "ppi_marketdata", "market": "BYMA",
        "instrument_type": "CEDEARS", "symbol": "XLV",
        "currency": "ARS", "currency_label": "Pesos",
        "currency_source": "ppi_instrument_catalog", "quality_issues": [],
        "indicator_eligible": True, "history_status": "sufficient",
        "observation_count": len(bars), "bars": bars,
    }


def test_ppi_indicators_are_computed_from_full_payload():
    result = json.loads(CalculatePPIIndicatorsTool().execute(
        payload=_payload(), source_call_id="call_abcdef1234"
    ))
    assert result["ok"] is True
    assert result["bar_count"] == 83
    assert result["indicators"]["sma_20"] is not None
    assert result["indicators"]["sma_50"] is not None
    assert result["indicators"]["sma_200"] is None
    assert result["indicators"]["rsi_14"] is not None
    assert result["indicators"]["macd"] is not None
    assert result["currency"] == "ARS"
    assert result["source_call_id"] == "call_abcdef1234"


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(indicator_eligible=False),
    lambda p: p.update(quality_issues=["invalid_ohlc"]),
    lambda p: p.update(currency="unknown"),
    lambda p: p.update(source="yahoo"),
    lambda p: p["bars"][0].update(volume=True),
    lambda p: p["bars"][0].update(close=-1),
    lambda p: p["bars"][1].update(date=p["bars"][0]["date"]),
    lambda p: p.update(observation_count=1),
])
def test_ppi_indicators_fail_closed_for_invalid_data(mutation):
    payload = _payload()
    mutation(payload)
    output = json.loads(CalculatePPIIndicatorsTool().execute(
        payload=payload, source_call_id="call_abcdef1234"
    ))
    assert output["ok"] is False


def test_ppi_indicators_do_not_use_yahoo(monkeypatch):
    import src.market_data
    monkeypatch.setattr(src.market_data, "fetch_market_data",
                        lambda **kwargs: pytest.fail("Yahoo/loader invoked"))
    result = json.loads(CalculatePPIIndicatorsTool().execute(
        payload=_payload(), source_call_id="call_abcdef1234"
    ))
    assert result["ok"]


@pytest.mark.parametrize("status", ["success", "ok"])
def test_wrapped_mcp_response_indicators(status):
    inner = _payload()
    inner["requested_symbol"] = "XLV.BA"
    result = json.loads(CalculatePPIIndicatorsTool().execute(
        payload={"status": status, "data": inner},
        source_call_id="call_ppi_abc123",
    ))
    assert result["ok"] is True
    assert result["symbol"] == "XLV"
    assert result["requested_symbol"] == "XLV.BA"
    assert result["indicators"]["sma_20"] is not None
    assert result["indicators"]["rsi_14"] is not None
    assert result["indicators"]["macd"] is not None


@pytest.mark.parametrize("wrapper", [
    {"status": "error", "data": _payload()},
    {"status": "success", "data": None},
    {"status": "success", "data": {"ok": False}},
    {"status": "success", "data": {"ok": True, "source": "yahoo"}},
])
def test_invalid_mcp_wrapper_fails_closed(wrapper):
    result = json.loads(CalculatePPIIndicatorsTool().execute(
        payload=wrapper, source_call_id="call_ppi_abc123",
    ))
    assert result["ok"] is False
