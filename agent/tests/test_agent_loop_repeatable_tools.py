"""Regression tests for parameter-dependent query tools in the agent loop."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agent.context import ContextBuilder
from src.agent.loop import AgentLoop
from src.agent.tools import BaseTool, ToolRegistry
from src.agent.trace import TraceWriter
from src.tools.fund_flow_tool import FundFlowTool
from src.tools.get_fundamentals_tool import GetFundamentalsTool
from src.tools.market_data_tool import MarketDataTool
from src.tools.market_screener_tool import MarketScreenerTool
from src.tools.symbol_search_tool import SymbolSearchTool


@pytest.mark.parametrize(
    ("tool_cls", "first_args", "second_args"),
    [
        (
            MarketDataTool,
            {
                "codes": ["AAPL.US"],
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
            {
                "codes": ["MSFT.US"],
                "start_date": "2025-01-01",
                "end_date": "2025-01-31",
            },
        ),
        (
            GetFundamentalsTool,
            {
                "symbols": ["AAPL.US"],
                "fields": ["roe"],
                "start": "2025-01-01",
                "end": "2025-01-31",
            },
            {
                "symbols": ["MSFT.US"],
                "fields": ["roe"],
                "start": "2025-01-01",
                "end": "2025-01-31",
            },
        ),
        (
            MarketScreenerTool,
            {"market": "us", "sort_by": "volume", "top_n": 5},
            {"market": "hk", "sort_by": "amount", "top_n": 10},
        ),
        (
            SymbolSearchTool,
            {"query": "Apple", "limit": 5},
            {"query": "Microsoft", "limit": 5},
        ),
    ],
)
def test_repeatable_query_executes_again_with_different_arguments(
    monkeypatch,
    tmp_path: Path,
    tool_cls: type,
    first_args: dict[str, object],
    second_args: dict[str, object],
) -> None:
    """A successful query must not suppress the next iteration's symbol."""
    calls: list[dict[str, object]] = []
    tool = tool_cls()

    def _execute(**kwargs: object) -> str:
        calls.append(kwargs)
        return json.dumps({"status": "ok"})

    monkeypatch.setattr(tool, "execute", _execute)
    registry = ToolRegistry()
    registry.register(tool)
    agent = AgentLoop(registry=registry, llm=SimpleNamespace(), max_iterations=2)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    agent.memory.run_dir = str(run_dir)
    trace = TraceWriter(run_dir)
    messages: list[dict[str, object]] = []
    react_trace: list[dict[str, object]] = []

    for iteration, (call_id, arguments) in enumerate(
        (("call_first", first_args), ("call_second", second_args)), start=1
    ):
        agent._process_tool_calls(
            [
                SimpleNamespace(
                    id=call_id,
                    name=tool.name,
                    arguments=arguments,
                )
            ],
            ContextBuilder,
            messages,
            trace,
            react_trace,
            iteration,
        )
    trace.close()

    assert [
        {key: value for key, value in call.items() if key != "run_dir"}
        for call in calls
    ] == [first_args, second_args]
    assert len(messages) == 2
    assert not any(
        event["type"] == "tool_skipped" for event in TraceWriter.read(run_dir)
    )


def test_cleared_result_replays_run_scoped_readonly_cache(monkeypatch, tmp_path: Path) -> None:
    """A non-repeatable readonly result cleared by microcompact is restored
    from this run's cache instead of repeating the external query.

    The result remains gated while readable. Once microcompact removes it, the
    exact call is reopened and a subsequent request restores the successful
    payload via ``tool_result_replayed``. This gives the model its evidence
    back without another external fetch and without weakening write-tool
    deduplication.
    """
    from src.agent.loop import KEEP_RECENT

    calls: list[dict[str, object]] = []
    tool = FundFlowTool()
    assert not tool.repeatable, "test needs a non-repeatable tool to have a gate at all"
    assert tool.is_readonly, "run-scoped replay is restricted to readonly tools"

    def _execute(**kwargs: object) -> str:
        calls.append(kwargs)
        return json.dumps({"status": "ok", "rows": ["x" * 200]})

    monkeypatch.setattr(tool, "execute", _execute)
    registry = ToolRegistry()
    registry.register(tool)
    agent = AgentLoop(registry=registry, llm=SimpleNamespace(), max_iterations=5)

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    agent.memory.run_dir = str(run_dir)
    trace = TraceWriter(run_dir)
    messages: list[dict[str, object]] = []
    react_trace: list[dict[str, object]] = []
    args = {"code": "600584.SH"}

    def _call(call_id: str, iteration: int) -> None:
        agent._process_tool_calls(
            [SimpleNamespace(id=call_id, name=tool.name, arguments=dict(args))],
            ContextBuilder,
            messages,
            trace,
            react_trace,
            iteration,
        )

    _call("call_1", 1)
    assert len(calls) == 1, "first call must execute"

    _call("call_2", 2)
    assert len(calls) == 1, "second call must be skipped while the result is readable"

    for i in range(KEEP_RECENT + 1):
        messages.append({
            "role": "tool",
            "tool_call_id": f"pad_{i}",
            "name": "padding_tool",
            "content": "y" * 200,
        })
    reopened = agent._microcompact_and_unblock(messages, trace, 3)
    assert tool.name in reopened, f"{tool.name} should have been re-opened, got {reopened}"

    _call("call_3", 4)
    trace.close()

    assert len(calls) == 1, "cleared readonly evidence must be restored without a second fetch"
    events = TraceWriter.read(run_dir)
    assert any(e["type"] == "microcompact_cleared" for e in events), (
        "the clear must leave a trace event"
    )
    assert any(e["type"] == "tool_result_replayed" for e in events), (
        "restoring compacted evidence must be explicit in the trace"
    )


class _ReplayableReadonlyTool(BaseTool):
    """Offline stand-in for read_url's replay-after-compaction contract."""

    name = "read_url"
    description = "offline replay test double"
    parameters = {"type": "object", "properties": {"url": {"type": "string"}}}
    repeatable = True
    is_readonly = True
    replay_after_compaction = True

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **kwargs: object) -> str:
        self.calls += 1
        return json.dumps(
            {
                "status": "ok",
                "url": kwargs["url"],
                "content": "evidence-" + ("x" * 200),
            }
        )


def test_repeatable_replay_is_once_per_context_loss_then_duplicate_is_skipped(
    tmp_path: Path,
) -> None:
    """A planner loop cannot consume the same restored payload forever.

    One compaction loss grants one replay of the exact cached result. If the
    model immediately asks for the same call again, it receives a synthetic
    skip telling it to use the restored payload; the external tool is never
    re-executed. A later *new* compaction loss may grant one fresh replay.
    """
    from src.agent.loop import KEEP_RECENT

    tool = _ReplayableReadonlyTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = AgentLoop(registry=registry, llm=SimpleNamespace(), max_iterations=8)

    run_dir = tmp_path / "replay_once"
    run_dir.mkdir()
    agent.memory.run_dir = str(run_dir)
    trace = TraceWriter(run_dir)
    messages: list[dict[str, object]] = []
    react_trace: list[dict[str, object]] = []
    args = {"url": "https://example.com/report"}

    def _call(call_id: str, iteration: int) -> None:
        agent._process_tool_calls(
            [SimpleNamespace(id=call_id, name=tool.name, arguments=dict(args))],
            ContextBuilder,
            messages,
            trace,
            react_trace,
            iteration,
        )

    def _pad(prefix: str) -> None:
        for i in range(KEEP_RECENT + 1):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"{prefix}_{i}",
                    "name": "padding_tool",
                    "content": "y" * 200,
                }
            )

    _call("call_1", 1)
    assert tool.calls == 1

    _pad("pad_a")
    reopened = agent._microcompact_and_unblock(messages, trace, 2)
    assert tool.name in reopened

    _call("call_2", 3)
    assert tool.calls == 1, "first post-compaction repeat must replay, not refetch"

    _call("call_3", 4)
    assert tool.calls == 1, "second identical request must not replay or refetch"
    skipped = json.loads(messages[-1]["content"])
    assert skipped["skipped"] is True
    assert "restored from the run-scoped replay cache" in skipped["reason"]
    assert "continue the analysis" in skipped["reason"]

    # A later, genuinely new context loss re-arms exactly one replay.
    _pad("pad_b")
    reopened = agent._microcompact_and_unblock(messages, trace, 5)
    assert tool.name in reopened
    _call("call_4", 6)
    trace.close()

    assert tool.calls == 1
    events = list(TraceWriter.read(run_dir))
    replayed = [event for event in events if event["type"] == "tool_result_replayed"]
    skipped_events = [event for event in events if event["type"] == "tool_skipped"]
    assert len(replayed) == 2, "each distinct context-loss event grants one replay"
    assert len(skipped_events) >= 1, "an immediate repeat after replay must be skipped"
