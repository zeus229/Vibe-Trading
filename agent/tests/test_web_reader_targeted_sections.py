"""Focused offline tests for selecting sections of long web documents."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.tools import web_reader_tool as reader


def _mock_jina(monkeypatch, text: str):
    calls = []

    def fake_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return SimpleNamespace(status_code=200, text=text)

    monkeypatch.setattr(reader.requests, "get", fake_get)
    return calls


def _direct_read(**kwargs):
    # Exercise the actual reader body while avoiding a live gateway and network.
    return json.loads(reader.read_url.__wrapped__(**kwargs))


def test_default_first_8000_characters_is_unchanged(monkeypatch):
    text = "Title: YPF\n" + "a" * 12000
    _mock_jina(monkeypatch, text)
    result = _direct_read(url="https://example.com/report")
    assert result["status"] == "ok"
    assert result["length"] == len(text)
    assert result["content"].startswith("Title: YPF\n")
    assert "truncated, total" in result["content"]
    assert len(result["content"]) < 8100
    assert "range_start" not in result
    assert "excerpts" not in result


def test_find_finds_sections_far_beyond_default_truncation(monkeypatch):
    text = "Title: YPF\n" + "a" * 15000 + "Revenue: 123" + "b" * 20000 + "Net income: 456"
    calls = _mock_jina(monkeypatch, text)
    result = _direct_read(
        url="https://example.com/report",
        find=["Revenue", "Net income"],
    )
    assert result["status"] == "ok"
    assert result["length"] == len(text)
    assert "Revenue: 123" in result["content"]
    assert "Net income: 456" in result["content"]
    assert "a" * 8000 not in result["content"]
    assert result["match_counts"] == {"Revenue": 1, "Net income": 1}
    assert [row["term"] for row in result["excerpts"]] == ["Revenue", "Net income"]
    assert result["excerpts"][0]["match_start"] == text.index("Revenue")
    assert len(result["content"]) <= 8000
    assert len(calls) == 1


def test_find_is_case_insensitive_and_preserves_source_offsets(monkeypatch):
    text = "START\n" + "a" * 9000 + "NET INCOME" + "b" * 10000
    _mock_jina(monkeypatch, text)
    result = _direct_read(url="https://example.com/report", find=["net income"])
    assert result["match_counts"]["net income"] == 1
    assert "NET INCOME" in result["content"]
    assert result["excerpts"][0]["match_start"] == text.index("NET INCOME")


def test_offset_reads_later_window_and_errors_on_out_of_bounds(monkeypatch):
    text = "Title: YPF\n" + "a" * 9500 + "Net income 123" + "b" * 8000
    _mock_jina(monkeypatch, text)
    start = text.index("Net income")
    result = _direct_read(url="https://example.com/report", start_char=start)
    assert result["status"] == "ok"
    assert result["content"].startswith("Net income 123")
    assert result["range_start"] == start
    assert result["range_end"] == start + 8000
    assert result["truncated"] is True
    invalid = _direct_read(url="https://example.com/report", start_char=len(text) + 1)
    assert invalid["status"] == "error"


@pytest.mark.parametrize(
    "selection",
    [
        {"find": []},
        {"find": [""]},
        {"find": ["a"] * 5},
        {"find": ["a" * 121]},
        {"find": "revenue"},
        {"find": ["Revenue"], "start_char": 10},
        {"start_char": -1},
        {"start_char": "100"},
        {"start_char": True},
    ],
)
def test_invalid_selection_fails_closed_without_network(monkeypatch, selection):
    def fail_get(*args, **kwargs):
        pytest.fail("Invalid selection should not trigger HTTP")

    monkeypatch.setattr(reader.requests, "get", fail_get)
    outcome = _direct_read(url="https://example.com/report", **selection)
    assert outcome["status"] == "error"


def test_not_found_returns_no_invented_financial_numbers(monkeypatch):
    _mock_jina(monkeypatch, "Title: Some report\n" + "x" * 12000)
    result = _direct_read(url="https://example.com/report", find=["net income"])
    assert result["status"] == "ok"
    assert result["content"] == ""
    assert result["match_counts"] == {"net income": 0}
    assert result["excerpts"] == []


def test_tool_schema_exposes_bounded_find_and_offset():
    spec = reader.WebReaderTool.parameters["properties"]
    assert spec["find"]["maxItems"] == 4
    assert spec["start_char"]["minimum"] == 0
    assert reader.WebReaderTool.name == "read_url"
