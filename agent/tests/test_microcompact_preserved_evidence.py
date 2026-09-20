"""Regression gates for compact analysis evidence surviving layer-1 microcompact."""

from __future__ import annotations

from src.agent.loop import _is_cleared, _microcompact
from src.tools.financial_rigor_tool import FinancialRigorTool
from src.tools.portfolio_tool import PortfolioSummaryTool
from src.tools.technical_indicator_tool import TechnicalIndicatorTool


def _tool(call_id: str, name: str, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "name": name,
        "content": content,
    }


def test_microcompact_preserves_named_high_value_evidence() -> None:
    messages = [
        {"role": "system", "content": "system"},
        _tool("p", "portfolio_summary", "p" * 500),
        _tool("t", "technical_indicators", "t" * 500),
        _tool("f", "financial_rigor", "f" * 500),
        _tool("m1", "get_market_data", "m" * 500),
        _tool("m2", "get_market_data", "m" * 500),
        _tool("m3", "get_market_data", "m" * 500),
        _tool("m4", "get_market_data", "m" * 500),
    ]

    _microcompact(
        messages,
        preserve_tools={"portfolio_summary", "technical_indicators", "financial_rigor"},
    )

    assert messages[1]["content"] == "p" * 500
    assert messages[2]["content"] == "t" * 500
    assert messages[3]["content"] == "f" * 500
    assert _is_cleared(messages[4]["content"])
    assert not _is_cleared(messages[-1]["content"])


def test_only_evidence_tools_opt_into_microcompact_preservation() -> None:
    assert PortfolioSummaryTool.preserve_during_microcompact is True
    assert TechnicalIndicatorTool.preserve_during_microcompact is True
    assert FinancialRigorTool.preserve_during_microcompact is True
