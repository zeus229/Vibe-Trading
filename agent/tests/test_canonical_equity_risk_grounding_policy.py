"""Policy regression: canonical equity risk figures retain tool-call provenance."""
from src.agent.context import _SYSTEM_PROMPT


def test_equity_risk_mcp_figures_require_real_tool_call_id_and_all_symbols():
    prompt = _SYSTEM_PROMPT
    assert "Canonical ACCIONES response completeness" in prompt
    assert "EXACT tool_call_id" in prompt
    assert "risk_contribution_pct.YPFD" in prompt
    assert "correlation_matrix.YPFD.GGAL" in prompt
    assert "all returned symbols" in prompt
    assert "Do not replace numeric arrays" in prompt
    assert "prefix `data.` only if" in prompt
