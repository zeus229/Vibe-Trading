from __future__ import annotations

import json
from types import SimpleNamespace

from src.agent.tools import ToolRegistry
from src.config.schema import AgentConfig, MCPServerConfig
from src.session.models import AuthMethod, Principal, Session
from src.tools.mcp import MCPServerAdapter, build_mcp_tool_wrappers
from src.tools.ppi_conversation_scope import (
    allow_ppi_conversation_tools,
    scope_asistente_casa_agent_config,
)


_BASE_READS = [
    "consultar_performance_cartera_scope",
    "consultar_attribution_cartera_scope",
]
_MACRO = "consultar_contexto_mercado_canonico"
_EQUITY_RISK = "consultar_riesgo_acciones_canonico"
_CONVERSATIONAL_READS = {
    "consultar_cartera_actual",
    "consultar_mercado_ppi_instrumento",
    _MACRO,
    _EQUITY_RISK,
}
_REMOTE_TOOLS = [
    "consultar_performance_cartera_scope",
    "consultar_attribution_cartera_scope",
    "consultar_cartera_actual",
    "consultar_mercado_ppi_instrumento",
    _MACRO,
    _EQUITY_RISK,
]


def _config(enabled_tools: list[str] | None = None) -> AgentConfig:
    return AgentConfig(
        mcp_servers={
            "asistente_casa": MCPServerConfig(
                type="stdio",
                command="unused-in-test",
                enabled_tools=list(enabled_tools or _BASE_READS),
            )
        }
    )


def _human_session() -> Session:
    return Session(
        title="Market review",
        owner=Principal(subject="shared-key-holder", auth_method=AuthMethod.SHARED_KEY),
    )


def test_only_owned_non_scheduled_sessions_are_conversational() -> None:
    assert allow_ppi_conversation_tools(_human_session())
    assert not allow_ppi_conversation_tools(None)
    assert not allow_ppi_conversation_tools(Session(title="CLI", owner=None))
    assert not allow_ppi_conversation_tools(
        Session(title="scheduled-research:daily", owner=_human_session().owner)
    )


def test_conversation_gets_only_macro_addition_and_original_reads_remain() -> None:
    base = _config()
    scoped = scope_asistente_casa_agent_config(base, conversational=True)

    assert set(scoped.mcp_servers["asistente_casa"].enabled_tools) == (
        set(_BASE_READS) | _CONVERSATIONAL_READS
    )
    assert scoped.mcp_servers["asistente_casa"].url == base.mcp_servers["asistente_casa"].url
    assert base.mcp_servers["asistente_casa"].enabled_tools == _BASE_READS


def test_scheduled_and_ownerless_allowlists_are_unchanged_and_strip_macro() -> None:
    for session in (None, Session(title="scheduled-research:daily")):
        assert not allow_ppi_conversation_tools(session)
        scoped = scope_asistente_casa_agent_config(_config(), conversational=False)
        assert scoped.mcp_servers["asistente_casa"].enabled_tools == _BASE_READS

    # A macro entry accidentally present in the shared config is still removed
    # from non-conversational scopes, while the two original reads are retained.
    scoped = scope_asistente_casa_agent_config(
        _config(_BASE_READS + [_MACRO, _EQUITY_RISK]), conversational=False
    )
    assert scoped.mcp_servers["asistente_casa"].enabled_tools == _BASE_READS


def test_wildcard_is_narrowed_before_any_session_can_discover_mcp_tools() -> None:
    for conversational in (False, True):
        scoped = scope_asistente_casa_agent_config(
            _config(["*"]), conversational=conversational
        )
        allowed = set(scoped.mcp_servers["asistente_casa"].enabled_tools)
        assert "*" not in allowed
        assert set(_BASE_READS) <= allowed
        assert (_MACRO in allowed) is conversational
        assert (_EQUITY_RISK in allowed) is conversational


def test_discovery_preserves_reads_and_denies_macro_direct_call_for_scheduled(monkeypatch) -> None:
    fake_tools = [
        SimpleNamespace(
            name=name,
            description=name,
            inputSchema={"type": "object", "properties": {}},
        )
        for name in _REMOTE_TOOLS
    ]

    async def list_tools(self):
        return fake_tools

    monkeypatch.setattr(MCPServerAdapter, "_list_tools", list_tools)

    scheduled = scope_asistente_casa_agent_config(_config(), conversational=False)
    wrappers = build_mcp_tool_wrappers(
        "asistente_casa", scheduled.mcp_servers["asistente_casa"]
    )
    registry = ToolRegistry()
    for wrapper in wrappers:
        registry.register(wrapper)

    discovered = {wrapper.name for wrapper in wrappers}
    assert discovered == {
        "mcp_asistente_casa_consultar_performance_cartera_scope",
        "mcp_asistente_casa_consultar_attribution_cartera_scope",
    }
    denied_risk = json.loads(
        registry.execute("mcp_asistente_casa_consultar_riesgo_acciones_canonico", {"window": 252})
    )
    assert denied_risk["status"] == "error"
    assert "not found" in denied_risk["error"]
    denied = json.loads(
        registry.execute(
            "mcp_asistente_casa_consultar_contexto_mercado_canonico", {}
        )
    )
    assert denied["status"] == "error"
    assert "not found" in denied["error"]


def test_conversation_discovery_adds_macro_without_changing_existing_reads(monkeypatch) -> None:
    fake_tools = [
        SimpleNamespace(
            name=name,
            description=name,
            inputSchema={"type": "object", "properties": {}},
        )
        for name in _REMOTE_TOOLS
    ]

    async def list_tools(self):
        return fake_tools

    monkeypatch.setattr(MCPServerAdapter, "_list_tools", list_tools)

    conversational = scope_asistente_casa_agent_config(_config(), conversational=True)
    wrappers = build_mcp_tool_wrappers(
        "asistente_casa", conversational.mcp_servers["asistente_casa"]
    )
    discovered = {wrapper.name.removeprefix("mcp_asistente_casa_") for wrapper in wrappers}
    assert discovered == set(_BASE_READS) | _CONVERSATIONAL_READS
    assert "mcp_asistente_casa_consultar_riesgo_acciones_canonico" in {w.name for w in wrappers}
