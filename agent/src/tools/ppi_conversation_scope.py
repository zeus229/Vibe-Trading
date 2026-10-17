"""Trusted per-run Asistente Casa MCP tool scope.

Never accept MCP server definitions or tool elevations from session overrides.
Only API sessions with a server-authenticated Principal (not scheduled jobs)
receive additional, explicitly read-only tools from the already configured
Asistente Casa MCP server. The persisted agent.json is never changed.
"""
from __future__ import annotations

from typing import Any

_CONVERSATIONAL_READS = frozenset({
    "consultar_cartera_actual",
    "consultar_mercado_ppi_instrumento",
    "consultar_contexto_mercado_canonico",
    "consultar_riesgo_acciones_canonico",
})
# These existing tools stay available for scheduled research.
_ORIGINAL_SCHEDULED_READS = frozenset({
    "consultar_performance_cartera_scope",
    "consultar_attribution_cartera_scope",
})

# The frozen comparison is deliberately pinned to one audited package. These
# names are never added to Scheduled Research or general conversations.
HERMES_COMPARISON_RUN_ID = "2026-10-15:cierre-supervisor:ecb8ce26"
HERMES_COMPARISON_DATE = "2026-10-15"
HERMES_COMPARISON_TITLE = f"vibe-hermes-comparison:{HERMES_COMPARISON_RUN_ID}"
_HERMES_PACKAGE_READS = frozenset({
    "consultar_paquete_hermes_manifest",
    "consultar_paquete_hermes_seccion",
})
HERMES_PACKAGE_SECTIONS = frozenset({
    "executive_summary", "portfolio", "performance", "changes", "risk",
    "liquidity", "alerts", "news", "research", "recommendations", "confidence",
})


def allow_ppi_conversation_tools(session: Any) -> bool:
    """Fail closed for background and unknown-owner sessions."""
    if session is None or getattr(session, "owner", None) is None:
        return False
    title = str(getattr(session, "title", "") or "").lower()
    # Scheduled sessions are created by the internal executor without owner.
    # Also deny by title as defense-in-depth if a future scheduler adds one.
    return not title.startswith(("scheduled-research:", "vibe-hermes-comparison:"))


def allow_hermes_comparison_tools(session: Any) -> bool:
    """Only the authenticated, explicitly named frozen-package session qualifies."""
    if session is None or getattr(session, "owner", None) is None:
        return False
    return str(getattr(session, "title", "") or "") == HERMES_COMPARISON_TITLE


def scope_asistente_casa_agent_config(
    config: Any, *, conversational: bool, hermes_comparison: bool = False
) -> Any:
    """Return a deep-copied config with a narrowed/extended MCP allowlist.

    Never expand '*' or introduce a new MCP server or credential. Unknown
    configured servers are left untouched.
    """
    config = config.model_copy(deep=True)
    for key in ("asistente-casa", "asistente_casa"):
        server = config.mcp_servers.get(key)
        if server is None:
            continue
        current = list(server.enabled_tools)
        if hermes_comparison:
            server.enabled_tools = sorted(_HERMES_PACKAGE_READS)
            continue
        if "*" in current:
            # Wildcards cannot prove a restricted scheduled baseline.
            # Keep fail-closed for these high-value canonical portfolio reads.
            server.enabled_tools = sorted(_ORIGINAL_SCHEDULED_READS)
            if not conversational:
                return config
            current = list(server.enabled_tools)
        if conversational:
            server.enabled_tools = list(dict.fromkeys(current + sorted(_CONVERSATIONAL_READS)))
        else:
            server.enabled_tools = [
                tool for tool in current
                if tool not in _CONVERSATIONAL_READS and tool not in _HERMES_PACKAGE_READS
            ]
    return config


__all__ = [
    "HERMES_COMPARISON_RUN_ID",
    "HERMES_COMPARISON_DATE",
    "HERMES_COMPARISON_TITLE",
    "HERMES_PACKAGE_SECTIONS",
    "allow_hermes_comparison_tools",
    "allow_ppi_conversation_tools",
    "scope_asistente_casa_agent_config",
]
