from __future__ import annotations

import json
from types import SimpleNamespace

from src.agent.tools import ToolRegistry
from src.config.schema import AgentConfig, MCPServerConfig
from src.session.models import AuthMethod, Principal, Session
from src.tools.mcp import MCPServerAdapter, build_mcp_tool_wrappers
from src.tools.ppi_conversation_scope import (
    HERMES_COMPARISON_RUN_ID,
    HERMES_COMPARISON_TITLE,
    allow_hermes_comparison_tools,
    allow_ppi_conversation_tools,
    scope_asistente_casa_agent_config,
)


_BASE_READS = [
    "consultar_performance_cartera_scope",
    "consultar_attribution_cartera_scope",
]
_MACRO = "consultar_contexto_mercado_canonico"
_EQUITY_RISK = "consultar_riesgo_acciones_canonico"
_HERMES_READS = {
    "consultar_paquete_hermes_manifest",
    "consultar_paquete_hermes_seccion",
}
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


def test_hermes_comparison_requires_server_allowlisted_attributable_identity(monkeypatch) -> None:
    title = HERMES_COMPARISON_TITLE
    shared = Principal(subject="shared-key-holder", auth_method=AuthMethod.SHARED_KEY)
    trusted = Principal(subject="trusted-operator", auth_method=AuthMethod.FEDERATED_IDENTITY)
    other = Principal(subject="other-operator", auth_method=AuthMethod.FEDERATED_IDENTITY)
    monkeypatch.delenv("VIBE_HERMES_COMPARISON_ALLOWED_SUBJECT", raising=False)
    assert not allow_hermes_comparison_tools(Session(title=title, owner=trusted))
    monkeypatch.setenv("VIBE_HERMES_COMPARISON_ALLOWED_SUBJECT", "trusted-operator")
    assert allow_hermes_comparison_tools(Session(title=title, owner=trusted))
    assert not allow_hermes_comparison_tools(Session(title=title, owner=shared))
    assert not allow_hermes_comparison_tools(Session(title=title, owner=other))
    assert not allow_hermes_comparison_tools(Session(title=title))
    assert not allow_hermes_comparison_tools(
        Session(title=f"vibe-hermes-comparison:{HERMES_COMPARISON_RUN_ID}:other", owner=trusted)
    )
    assert not allow_hermes_comparison_tools(
        Session(title=f"scheduled-research:{title}", owner=trusted)
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


def test_hermes_comparison_replaces_asistente_casa_allowlist_and_preserves_base() -> None:
    base = _config(["*"])
    scoped = scope_asistente_casa_agent_config(
        base, conversational=False, hermes_comparison=True
    )
    assert set(scoped.mcp_servers["asistente_casa"].enabled_tools) == _HERMES_READS
    assert base.mcp_servers["asistente_casa"].enabled_tools == ["*"]


def test_hermes_tools_are_removed_from_all_non_comparison_sessions() -> None:
    scoped = scope_asistente_casa_agent_config(
        _config(list(_BASE_READS) + sorted(_HERMES_READS)), conversational=False
    )
    assert not (_HERMES_READS & set(scoped.mcp_servers["asistente_casa"].enabled_tools))


def test_frozen_hermes_reads_force_run_id_and_only_expose_analysis_sections(monkeypatch) -> None:
    from src.tools.ppi_conversation_scope import HERMES_PACKAGE_SECTIONS

    schemas = {
        "consultar_paquete_hermes_manifest": {
            "type": "object",
            "properties": {"report_type": {"type": "string"}, "date": {"type": "string"},
                           "run_id": {"type": "string"}},
        },
        "consultar_paquete_hermes_seccion": {
            "type": "object",
            "properties": {"run_id": {"type": "string"},
                           "section": {"type": "string", "enum": sorted(HERMES_PACKAGE_SECTIONS)}},
        },
        "consultar_cartera_actual": {"type": "object", "properties": {}},
    }
    fake_tools = [
        SimpleNamespace(name=name, description=name, inputSchema=schema)
        for name, schema in schemas.items()
    ]

    async def list_tools(self):
        return fake_tools

    monkeypatch.setattr(MCPServerAdapter, "_list_tools", list_tools)
    config = scope_asistente_casa_agent_config(
        _config(["*"]), conversational=False, hermes_comparison=True
    )
    fixed = {
        "consultar_paquete_hermes_manifest": {
            "report_type": "cierre", "date": "2026-10-15", "run_id": HERMES_COMPARISON_RUN_ID,
        },
        "consultar_paquete_hermes_seccion": {"run_id": HERMES_COMPARISON_RUN_ID},
    }
    wrappers = build_mcp_tool_wrappers(
        "asistente_casa", config.mcp_servers["asistente_casa"], fixed_arguments=fixed,
        argument_allowlists={"consultar_paquete_hermes_seccion": {
            "section": set(HERMES_PACKAGE_SECTIONS),
        }},
    )
    assert {tool._spec.remote_name for tool in wrappers} == _HERMES_READS
    calls = []
    for tool in wrappers:
        tool._adapter.call_tool = lambda name, arguments, **kwargs: (
            calls.append((name, arguments)) or {"ok": True, "data": {"evidence_id": "pkg#section"}}
        )

    manifest_tool = next(t for t in wrappers if t._spec.remote_name.endswith("_manifest"))
    section_tool = next(t for t in wrappers if t._spec.remote_name.endswith("_seccion"))
    manifest_result = json.loads(
        manifest_tool.execute(run_id="other", date="2026-10-16", report_type="mediarueda")
    )
    section_result = json.loads(section_tool.execute(run_id="other", section="performance"))
    assert manifest_result["data"]["evidence_id"] == "pkg#section"
    assert section_result["data"]["evidence_id"] == "pkg#section"
    assert manifest_tool.replay_after_compaction is True
    assert section_tool.replay_after_compaction is True
    rejected = json.loads(section_tool.execute(run_id="other", section="recommendation_tracking"))
    assert rejected["error"] == "argument_not_allowed:section"
    assert calls == [
        ("consultar_paquete_hermes_manifest", {
            "run_id": HERMES_COMPARISON_RUN_ID, "date": "2026-10-15", "report_type": "cierre",
        }),
        ("consultar_paquete_hermes_seccion", {
            "run_id": HERMES_COMPARISON_RUN_ID, "section": "performance",
        }),
    ]
    assert set(section_tool.parameters["properties"]["section"]["enum"]) == HERMES_PACKAGE_SECTIONS


def test_comparison_registry_excludes_local_live_tools_and_binds_manifest(monkeypatch) -> None:
    from src.tools import build_registry
    from src.tools.ppi_conversation_scope import HERMES_PACKAGE_SECTIONS

    fake_tools = [
        SimpleNamespace(name=name, description=name, inputSchema={
            "type": "object",
            "properties": ({"date": {"type": "string"}, "run_id": {"type": "string"}}
                           if name.endswith("manifest") else {"run_id": {"type": "string"},
                                                                "section": {"type": "string"}}),
        })
        for name in sorted(_HERMES_READS | {"consultar_cartera_actual"})
    ]

    async def list_tools(self):
        return fake_tools

    monkeypatch.setattr(MCPServerAdapter, "_list_tools", list_tools)
    config = scope_asistente_casa_agent_config(
        _config(["*"]), conversational=False, hermes_comparison=True
    )
    allowed = [
        "mcp_asistente_casa_consultar_paquete_hermes_manifest",
        "mcp_asistente_casa_consultar_paquete_hermes_seccion",
    ]
    fixed = {
        "consultar_paquete_hermes_manifest": {
            "date": "2026-10-15", "run_id": HERMES_COMPARISON_RUN_ID,
        },
        "consultar_paquete_hermes_seccion": {"run_id": HERMES_COMPARISON_RUN_ID},
    }
    registry = build_registry(
        agent_config=config,
        tool_allowlist=allowed,
        mcp_fixed_arguments={"asistente_casa": fixed},
        mcp_argument_allowlists={"asistente_casa": {
            "consultar_paquete_hermes_seccion": {"section": HERMES_PACKAGE_SECTIONS},
        }},
    )
    assert registry.tool_names == allowed
    assert registry.get("portfolio_risk_xray") is None
    assert registry.get("mcp_asistente_casa_consultar_cartera_actual") is None
    tool = registry.get(allowed[0])
    calls = []
    tool._adapter.call_tool = lambda name, args, **kwargs: (
        calls.append((name, args)) or {"ok": True, "data": {"run_id": args["run_id"]}}
    )
    result = json.loads(tool.execute(date="2026-10-16", run_id="other"))
    assert result["data"]["run_id"] == HERMES_COMPARISON_RUN_ID
    assert calls == [("consultar_paquete_hermes_manifest", {
        "date": "2026-10-15", "run_id": HERMES_COMPARISON_RUN_ID,
    })]


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
