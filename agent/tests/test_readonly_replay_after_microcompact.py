from __future__ import annotations

import threading
from types import SimpleNamespace

from src.agent.loop import AgentLoop
from src.agent.tool_progress import ToolProgress
from src.tools.web_reader_tool import WebReaderTool


class _Registry:
    def __init__(
        self,
        *,
        readonly=True,
        repeatable=False,
        deterministic=False,
        replay_after_compaction=False,
    ):
        self.tool = SimpleNamespace(
            is_readonly=readonly,
            repeatable=repeatable,
            deterministic=deterministic,
            replay_after_compaction=replay_after_compaction,
        )
        self.execute_calls = 0

    def get(self, name):
        return self.tool

    def execute(self, name, args):
        self.execute_calls += 1
        return '{"status":"ok","value":"fresh"}'


class _Context:
    @staticmethod
    def format_tool_result(call_id, name, result):
        return {"role": "tool", "tool_call_id": call_id, "name": name, "content": result}


class _Trace:
    def __init__(self):
        self.events = []

    def write(self, event):
        self.events.append(event)


def _loop(registry=None):
    loop = object.__new__(AgentLoop)
    loop.registry = registry or _Registry()
    loop.memory = SimpleNamespace(run_dir="/tmp/test-run")
    loop._called_ok = set()
    loop._successful_call_keys = {}
    loop._called_identical = {}
    loop._readonly_replay_cache = {}
    loop._readonly_replay_ready = set()
    loop._grounding = None
    loop._cancel_event = threading.Event()
    loop._event_callback = None
    loop._tool_progress = ToolProgress()
    return loop


def test_compaction_marks_lost_readonly_result_for_run_scoped_replay():
    loop = _loop()
    key = loop._identical_call_key("read_url", {"url": "https://example.test/report"})
    assert key is not None
    loop._called_ok.add(key)
    loop._readonly_replay_cache[key] = '{"status":"ok","body":"report"}'
    reopened = loop._unblock_lost_readonly_results([], {key})
    assert reopened == ["read_url"]
    assert key not in loop._called_ok
    assert key in loop._readonly_replay_ready


def test_replay_restores_result_without_external_execution_or_stall_increment():
    registry = _Registry()
    loop = _loop(registry)
    args = {"url": "https://example.test/report"}
    key = loop._identical_call_key("read_url", args)
    assert key is not None
    cached = '{"status":"ok","body":"report"}'
    loop._readonly_replay_cache[key] = cached
    loop._readonly_replay_ready.add(key)
    tc = SimpleNamespace(name="read_url", arguments=args, id="replay-1")
    messages = []
    trace = _Trace()
    react_trace = []
    loop._process_tool_calls([tc], _Context(), messages, trace, react_trace, 22)
    assert registry.execute_calls == 0
    assert messages[-1]["content"] == cached
    assert key in loop._called_ok
    assert key not in loop._readonly_replay_ready
    assert trace.events[-1]["type"] == "tool_result_replayed"
    assert loop._tool_progress.finish_iteration() is False
    assert loop._tool_progress.stalled_iterations == 0


def test_repeatable_requires_explicit_compaction_replay_opt_in():
    loop = _loop(_Registry())
    ordinary_repeatable = _Registry(repeatable=True).get("poll")
    assert loop._readonly_replay_allowed(ordinary_repeatable, {}) is False

    replayable_repeatable = _Registry(
        repeatable=True,
        replay_after_compaction=True,
    ).get("read_url")
    assert loop._readonly_replay_allowed(replayable_repeatable, {"url": "x"}) is True


def test_no_cache_bypasses_repeatable_compaction_replay():
    loop = _loop(_Registry())
    replayable_repeatable = _Registry(
        repeatable=True,
        replay_after_compaction=True,
    ).get("read_url")
    assert (
        loop._readonly_replay_allowed(
            replayable_repeatable,
            {"url": "x", "no_cache": True},
        )
        is False
    )


def test_repeatable_opt_in_replays_only_after_compaction_loss():
    registry = _Registry(repeatable=True, replay_after_compaction=True)
    loop = _loop(registry)
    args = {"url": "https://example.test/report"}
    tc1 = SimpleNamespace(name="read_url", arguments=args, id="call-1")
    messages = []
    trace = _Trace()
    react_trace = []

    loop._process_tool_calls([tc1], _Context(), messages, trace, react_trace, 1)
    assert registry.execute_calls == 1

    # While the original result is still visible, repeatable keeps its normal
    # semantics and may execute again.
    tc2 = SimpleNamespace(name="read_url", arguments=args, id="call-2")
    loop._process_tool_calls([tc2], _Context(), messages, trace, react_trace, 2)
    assert registry.execute_calls == 2

    key = loop._identical_call_key("read_url", args)
    assert key is not None
    # Simulate compaction removing every readable successful copy.
    reopened = loop._unblock_lost_readonly_results([], {key})
    assert reopened == ["read_url"]
    assert key in loop._readonly_replay_ready

    tc3 = SimpleNamespace(name="read_url", arguments=args, id="call-3")
    loop._process_tool_calls([tc3], _Context(), messages, trace, react_trace, 3)
    assert registry.execute_calls == 2
    assert any(event["type"] == "tool_result_replayed" for event in trace.events)


def test_web_reader_declares_repeatable_compaction_replay_contract():
    tool = WebReaderTool()
    assert tool.is_readonly is True
    assert tool.repeatable is True
    assert tool.replay_after_compaction is True
    assert tool.deterministic is False


def test_mutating_tools_and_existing_deterministic_cache_stay_separate():
    loop = _loop(_Registry(readonly=False))
    assert loop._readonly_replay_allowed(loop.registry.get("write_file"), {}) is False
    deterministic = _Registry(deterministic=True).get("financial_rigor")
    assert loop._readonly_replay_allowed(deterministic, {}) is False


def test_context_restore_resets_only_the_current_stall_chain():
    progress = ToolProgress()
    assert progress.finish_iteration() is False
    assert progress.stalled_iterations == 1
    progress.mark_context_restored()
    assert progress.finish_iteration() is False
    assert progress.stalled_iterations == 0
    assert progress.finish_iteration() is False
    assert progress.stalled_iterations == 1
