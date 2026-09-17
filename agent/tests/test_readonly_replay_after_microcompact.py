from __future__ import annotations

import threading
from types import SimpleNamespace

from src.agent.loop import AgentLoop
from src.agent.tool_progress import ToolProgress


class _Registry:
    def __init__(self, *, readonly=True, repeatable=False, deterministic=False):
        self.tool = SimpleNamespace(
            is_readonly=readonly,
            repeatable=repeatable,
            deterministic=deterministic,
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


def test_no_cache_and_repeatable_reads_are_not_replayed():
    loop = _loop(_Registry())
    tool = loop.registry.get("read_url")
    assert loop._readonly_replay_allowed(tool, {"url": "x", "no_cache": True}) is False
    repeatable = _Registry(repeatable=True).get("poll")
    assert loop._readonly_replay_allowed(repeatable, {}) is False


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
