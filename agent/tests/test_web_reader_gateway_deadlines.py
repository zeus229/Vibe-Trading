"""Web-reader gateway deadline regressions; no external network access."""

import json
import threading
import time

import pytest

from src.data_gateway.config import Config
from src.data_gateway.service import MarketDataGateway
from src.tools.web_reader_tool import WebReaderTool


@pytest.fixture
def reader_gateway(tmp_path, monkeypatch):
    """Patch only the process-wide gateway locator; retain the real source pool."""
    import src.data_gateway as gateway_module

    gateway = MarketDataGateway(
        Config(
            cache_dir=tmp_path,
            source_timeout=0.02,
            request_timeout=0.07,
            web_reader_source_timeout=0.35,
            web_reader_request_timeout=0.45,
            workers=1,
        ),
        {},
    )
    monkeypatch.setattr(gateway_module, "get_gateway", lambda: gateway)
    yield gateway
    gateway.close()


def test_slow_web_document_uses_its_own_budget_without_changing_general_deadlines(
    reader_gateway, monkeypatch,
):
    """A valid 100-ms document must outlive a normal 20-ms market-data source."""
    import src.tools.web_reader_tool as module

    observed = []

    def get(url, *, headers, timeout):
        observed.append((url, headers, timeout))
        time.sleep(0.10)

        class Response:
            status_code = 200
            text = "Title: Research report\nVerified public text"

        return Response()

    monkeypatch.setattr(module.requests, "get", get)
    result = json.loads(WebReaderTool().execute(url="https://example.org/report"))
    assert result["status"] == "ok"
    assert result["title"] == "Research report"
    assert result["content"].endswith("Verified public text")
    assert len(observed) == 1
    assert observed[0][0] == "https://r.jina.ai/https://example.org/report"
    assert 0 < observed[0][2] <= reader_gateway.config.web_reader_source_timeout
    assert reader_gateway.config.source_timeout == 0.02
    assert reader_gateway.config.request_timeout == 0.07


def test_web_document_timeout_is_bounded_and_retains_occupied_slot(reader_gateway, monkeypatch):
    """A stalled external fetch remains in the shared slot until it actually exits."""
    import src.tools.web_reader_tool as module

    entered, release = threading.Event(), threading.Event()
    observed = []

    def get(url, *, headers, timeout):
        observed.append(timeout)
        entered.set()
        release.wait(1)

        class Response:
            status_code = 200
            text = "Title: Slow source"

        return Response()

    monkeypatch.setattr(module.requests, "get", get)
    try:
        started = time.monotonic()
        response = json.loads(WebReaderTool().execute(url="https://example.org/slow"))
        elapsed = time.monotonic() - started
        assert entered.is_set()
        assert response["status"] == "error"
        assert response["error_code"] == "data_gateway_unavailable"
        assert elapsed < reader_gateway.config.web_reader_request_timeout
        assert observed and 0 < observed[0] <= reader_gateway.config.web_reader_source_timeout
        assert not reader_gateway.source_slots.acquire(blocking=False)
    finally:
        release.set()


def test_reader_preserves_upstream_http_error_without_retry(reader_gateway, monkeypatch):
    import src.tools.web_reader_tool as module

    calls = []

    def get(url, *, headers, timeout):
        calls.append(url)

        class Response:
            status_code = 429
            text = "Request rate limited"

        return Response()

    monkeypatch.setattr(module.requests, "get", get)
    result = json.loads(WebReaderTool().execute(url="https://example.org/limited"))
    assert result["status"] == "error"
    assert "HTTP 429" in result["error"]
    assert len(calls) == 1


def test_reader_rejects_private_targets_without_fetch(reader_gateway, monkeypatch):
    import src.tools.web_reader_tool as module

    def must_not_fetch(*args, **kwargs):
        pytest.fail("private target was forwarded to the external reader")

    monkeypatch.setattr(module.requests, "get", must_not_fetch)
    result = json.loads(WebReaderTool().execute(url="http://127.0.0.1/secret"))
    assert result["status"] == "error"
    assert result["error"] == "target URL is not allowed"
