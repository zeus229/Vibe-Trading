"""Focused isolation and calculation contracts for Asistente Casa PPI research."""
from __future__ import annotations

import json
import asyncio
from datetime import date, timedelta
from pathlib import Path
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
        "instrument_type": "CEDEARS", "symbol": "XLV", "requested_symbol": "XLV.BA",
        "currency": "ARS", "currency_label": "Pesos",
        "currency_source": "ppi_instrument_catalog", "quality_issues": [],
        "indicator_eligible": True, "history_status": "sufficient",
        "observation_count": len(bars), "bars": bars,
    }



@pytest.fixture
def observed_ppi(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.tools.ppi_indicators_tool.get_sessions_dir", lambda: tmp_path)

    def make_tool(data, *, status="ok"):
        directory = tmp_path / "test-session"
        directory.mkdir(exist_ok=True)
        wrapper = {"status": status, "data": data}
        rows = [
            {"type": "start"},
            {"type": "tool_result", "tool": "mcp_asistente_casa_consultar_mercado_ppi_instrumento",
             "call_id": "call_abcdef1234", "status": "ok", "result": wrapper},
            {"type": "tool_call", "tool": "calculate_ppi_indicators",
             "call_id": "call_calc1234", "args": {}},
        ]
        (directory / "trace.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        return CalculatePPIIndicatorsTool(default_session_id="test-session"), wrapper

    return make_tool


def test_ppi_indicators_are_computed_from_real_mcp_wrapper(observed_ppi):
    tool, wrapper = observed_ppi(_payload())
    result = json.loads(tool.execute(_runtime_call_id="call_calc1234"))
    assert result["ok"] is True
    assert result["bar_count"] == 83
    assert result["indicators"]["sma_20"] is not None
    assert result["indicators"]["sma_50"] is not None
    assert result["indicators"]["sma_200"] is None
    assert result["indicators"]["rsi_14"] is not None
    assert result["indicators"]["macd"] is not None
    assert result["currency"] == "ARS"
    assert result["source_call_id"] == "call_abcdef1234"
    assert result["calculation_call_id"] == "call_calc1234"
    assert result["symbol"] == "XLV.BA"
    assert result["ppi_symbol"] == "XLV"


def test_ppi_indicator_values_are_grounded_to_requested_byma_identity(observed_ppi, tmp_path):
    from src.agent.grounding import GroundingLedger

    tool, _ = observed_ppi(_payload())
    result = json.loads(tool.execute(symbol="XLV.BA", instrument_type="CEDEARS",
                                     _runtime_call_id="call_calc1234"))
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analizá XLV.BA")
    ledger.ingest_tool_result(
        tool_name="calculate_ppi_indicators",
        arguments={"symbol": "XLV.BA", "instrument_type": "CEDEARS"},
        result=json.dumps(result), call_id="call_calc1234", success=True,
    )
    values = [record for record in ledger._evidence if record.field == "indicators.sma_20"]
    assert len(values) == 1
    assert values[0].symbol == "XLV.BA"
    assert values[0].identity_scope == "entity"


@pytest.mark.parametrize("status", ["ok", "success", "available"])
def test_wrapped_mcp_response_indicators(status, observed_ppi):
    tool, _ = observed_ppi(_payload(), status=status)
    result = json.loads(tool.execute(symbol="XLV.BA", _runtime_call_id="call_calc1234"))
    assert result["ok"] is True
    assert result["symbol"] == "XLV.BA"
    assert result["ppi_symbol"] == "XLV"
    assert result["indicators"]["sma_20"] is not None
    assert result["indicators"]["rsi_14"] is not None
    assert result["indicators"]["macd"] is not None


@pytest.mark.parametrize("status,data", [
    ("error", _payload()),
    ("success", None),
    ("success", {"ok": False}),
    ("success", {"ok": True, "source": "yahoo"}),
])
def test_invalid_mcp_wrapper_fails_closed(status, data, observed_ppi):
    tool, _ = observed_ppi(data, status=status)
    result = json.loads(tool.execute(_runtime_call_id="call_calc1234"))
    assert result["ok"] is False


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
def test_ppi_indicators_fail_closed_for_invalid_data(mutation, observed_ppi):
    payload = _payload()
    mutation(payload)
    tool, wrapper = observed_ppi(payload)
    output = json.loads(tool.execute(_runtime_call_id="call_calc1234"))
    assert output["ok"] is False


def test_ppi_indicators_do_not_use_yahoo(monkeypatch, observed_ppi):
    import src.market_data
    monkeypatch.setattr(src.market_data, "fetch_market_data",
                        lambda **kwargs: pytest.fail("Yahoo/loader invoked"))
    tool, wrapper = observed_ppi(_payload())
    result = json.loads(tool.execute(_runtime_call_id="call_calc1234"))
    assert result["ok"]


def test_ppi_indicators_reject_fabricated_or_stale_bars(observed_ppi, tmp_path):
    tool, wrapper = observed_ppi(_payload())
    forged = json.loads(json.dumps(wrapper))
    forged["data"]["bars"][-1]["close"] += 1
    trusted = json.loads(tool.execute(payload=forged, source_call_id="call_wrong",
                                      _runtime_call_id="call_calc1234"))
    assert trusted["ok"] is True
    assert trusted["latest_close"] == wrapper["data"]["bars"][-1]["close"]
    assert not json.loads(tool.execute(symbol="MSFT.BA", _runtime_call_id="call_calc1234"))["ok"]
    with (tmp_path / "test-session/trace.jsonl").open("a") as stream:
        stream.write(json.dumps({"type": "start"}) + "\n")
    assert not json.loads(tool.execute(_runtime_call_id="call_calc1234"))["ok"]


def test_ppi_indicators_reject_ambiguous_instruments(observed_ppi, tmp_path):
    tool, wrapper = observed_ppi(_payload())
    other = json.loads(json.dumps(wrapper))
    other["data"]["requested_symbol"] = "MSFT.BA"
    other["data"]["symbol"] = "MSFT"
    with (tmp_path / "test-session/trace.jsonl").open("a") as stream:
        stream.write(json.dumps({"type": "tool_result",
                                 "tool": "mcp_asistente_casa_consultar_mercado_ppi_instrumento",
                                 "call_id": "call_msft", "status": "ok", "result": other}) + "\n")
    assert not json.loads(tool.execute(_runtime_call_id="call_calc1234"))["ok"]
    selected = json.loads(tool.execute(symbol="XLV.BA", instrument_type="CEDEARS",
                                       _runtime_call_id="call_calc1234"))
    assert selected["ok"] is True
    assert selected["source_call_id"] == "call_abcdef1234"


def test_ppi_indicator_tool_is_registered_for_session_only(observed_ppi):
    from src.tools import build_registry

    tool, wrapper = observed_ppi(_payload())
    assert tool.session_id == "test-session"
    registry = build_registry(session_id="test-session")
    assert registry.get("calculate_ppi_indicators").session_id == "test-session"
    result = json.loads(registry.execute("calculate_ppi_indicators", {}))
    assert result["ok"] is False
    result = json.loads(registry.execute("calculate_ppi_indicators", {
        "_runtime_call_id": "call_calc1234",
    }))
    assert result["ok"] is True


def test_agent_loop_injects_calculator_call_id():
    from src.agent.loop import AgentLoop

    class Registry:
        def execute(self, name, args):
            assert name == "calculate_ppi_indicators"
            assert args["_runtime_call_id"] == "call_host_exact"
            return '{"ok": true}'

    loop = AgentLoop.__new__(AgentLoop)
    loop.registry = Registry()
    loop._is_tool_readonly = lambda name: True
    loop._emit = lambda *args: None
    result, elapsed = loop._invoke_tool("calculate_ppi_indicators", {}, call_id="call_host_exact")
    assert json.loads(result)["ok"] is True


def test_real_session_service_scopes_api_and_scheduled_attempts(tmp_path, monkeypatch):
    from src.agent.tools import ToolRegistry
    from src.session.events import EventBus
    from src.session.models import Attempt, AuthMethod, Principal
    from src.session.service import SessionService
    from src.session.store import SessionStore

    class Index:
        def index_session(self, *args): pass
        def index_message(self, *args): pass

    seen = []

    def build_registry(**kwargs):
        allowed = kwargs["agent_config"].mcp_servers["asistente-casa"].enabled_tools
        registry = ToolRegistry()
        registry._tools["calculate_ppi_indicators"] = object()
        seen.append({"allowed": allowed, "registry": registry})
        return registry

    class Agent:
        def __init__(self, *, registry, **kwargs):
            seen[-1]["has_indicator"] = registry.get("calculate_ppi_indicators") is not None

        def run(self, **kwargs):
            return {"status": "success", "content": "ok"}

    monkeypatch.setattr("src.session.service.get_shared_index", lambda: Index())
    monkeypatch.setattr("src.tools.build_registry", build_registry)
    monkeypatch.setattr("src.agent.loop.AgentLoop", Agent)
    monkeypatch.setattr("src.providers.chat.ChatLLM", lambda: object())
    monkeypatch.setattr("src.memory.persistent.PersistentMemory", lambda: object())
    monkeypatch.setattr("src.config.loader.load_runtime_agent_config", lambda overrides=None: _config())
    service = SessionService(store=SessionStore(tmp_path / "sessions"),
                             event_bus=EventBus(), runs_dir=tmp_path / "runs")
    principal = Principal(subject="shared-key-holder", auth_method=AuthMethod.SHARED_KEY)
    interactive = service.create_session(title="PPI audit", owner=principal)
    scheduled = service.create_session(title="scheduled-research:job")

    async def exercise():
        for session in (interactive, scheduled):
            await service._run_with_agent(Attempt(session_id=session.session_id, prompt="test"),
                                          messages=[], session_config={})

    asyncio.run(exercise())
    assert "consultar_cartera_actual" in seen[0]["allowed"]
    assert "consultar_mercado_ppi_instrumento" in seen[0]["allowed"]
    assert seen[0]["has_indicator"] is True
    assert "consultar_cartera_actual" not in seen[1]["allowed"]
    assert "consultar_mercado_ppi_instrumento" not in seen[1]["allowed"]
    assert seen[1]["has_indicator"] is False
