"""Web-reader gateway deadline regressions; no external network access."""

from concurrent.futures import ThreadPoolExecutor
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
            web_reader_source_timeout=0.20,
            web_reader_request_timeout=0.45,
            workers=1,
        ),
        {},
    )
    monkeypatch.setattr(gateway_module, "get_gateway", lambda: gateway)
    yield gateway
    gateway.close()


def test_slow_web_document_uses_its_own_budget_without_changing_general_deadlines(
    reader_gateway, monkeypatch
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


def test_web_document_timeout_is_bounded_and_retains_occupied_slot(
    reader_gateway, monkeypatch
):
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
        # The caller must return around the source deadline, not wait for
        # the longer request budget. Allow generous scheduler jitter.
        assert elapsed < reader_gateway.config.web_reader_source_timeout + 0.12
        assert elapsed < reader_gateway.config.web_reader_request_timeout
        assert (
            observed
            and 0 < observed[0] <= reader_gateway.config.web_reader_source_timeout
        )
        assert not reader_gateway.source_slots.acquire(blocking=False)
        assert not reader_gateway.document_slots.acquire(blocking=False)
    finally:
        release.set()


def test_document_source_deadline_precedes_longer_request_deadline(
    tmp_path, monkeypatch
):
    """A blocked fetch returns at the 100ms source limit, not the 250ms request limit."""
    import src.data_gateway as gateway_module
    import src.tools.web_reader_tool as module

    entered = threading.Event()
    release = threading.Event()
    gateway = MarketDataGateway(
        Config(
            cache_dir=tmp_path,
            workers=1,
            source_timeout=0.02,
            request_timeout=0.05,
            web_reader_source_timeout=0.10,
            web_reader_request_timeout=0.25,
        ),
        {},
    )
    monkeypatch.setattr(gateway_module, "get_gateway", lambda: gateway)

    def get(url, *, headers, timeout):
        entered.set()
        release.wait(1)

        class Response:
            status_code = 200
            text = "Title: Late document"

        return Response()

    monkeypatch.setattr(module.requests, "get", get)
    try:
        started = time.monotonic()
        result = json.loads(WebReaderTool().execute(url="https://example.org/late"))
        elapsed = time.monotonic() - started
        assert entered.is_set()
        assert result["error_code"] == "data_gateway_unavailable"
        assert elapsed < 0.20
        assert not gateway.source_slots.acquire(blocking=False)
        assert not gateway.document_slots.acquire(blocking=False)
    finally:
        release.set()
        gateway.close()


def test_reader_preserves_upstream_http_error_without_retry(
    reader_gateway, monkeypatch
):
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


def test_reader_rejects_private_targets_without_fetch(
    reader_gateway, monkeypatch
):
    import src.tools.web_reader_tool as module

    def must_not_fetch(*args, **kwargs):
        pytest.fail("private target was forwarded to the external reader")

    monkeypatch.setattr(module.requests, "get", must_not_fetch)
    result = json.loads(WebReaderTool().execute(url="http://127.0.0.1/secret"))
    assert result["status"] == "error"
    assert result["error"] == "target URL is not allowed"


def test_slow_document_does_not_hold_shared_coordinators(tmp_path, monkeypatch):
    """An admitted slow document cannot starve ordinary requests or a second coordinator."""
    import src.data_gateway as gateway_module
    import src.tools.web_reader_tool as module
    from src.data_gateway.reads import read

    entered, release = threading.Event(), threading.Event()
    gateway = MarketDataGateway(
        Config(
            cache_dir=tmp_path,
            workers=3,
            source_timeout=0.08,
            request_timeout=0.2,
            web_reader_source_timeout=0.4,
            web_reader_request_timeout=0.5,
        ),
        {},
    )
    monkeypatch.setattr(gateway_module, "get_gateway", lambda: gateway)

    def get(url, *, headers, timeout):
        entered.set()
        release.wait(1)

        class Response:
            status_code = 200
            text = "Title: Released source"

        return Response()

    monkeypatch.setattr(module.requests, "get", get)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(WebReaderTool().execute, url="https://example.org/slow")
            assert entered.wait(1)
            # An additional long reader is rejected without consuming another
            # coordinator or waiting for its own long timeout.
            second_started = time.monotonic()
            second = json.loads(
                WebReaderTool().execute(url="https://example.org/another")
            )
            assert second["error_code"] == "data_gateway_unavailable"
            assert "busy" in second["error"]
            assert time.monotonic() - second_started < 0.1

            # Both shared coordinators and two source slots remain available.
            started = time.monotonic()
            normal = read(
                gateway,
                "test:ordinary",
                {},
                lambda: {"ok": True},
                cacheable=False,
            )
            assert normal == {"ok": True}
            assert time.monotonic() - started < gateway.config.request_timeout
            assert not first.done()
            release.set()
            assert json.loads(first.result(timeout=1))["status"] == "ok"
    finally:
        release.set()
        gateway.close()


def test_expired_parent_deadline_prevents_any_http_dispatch(
    reader_gateway, monkeypatch
):
    """A nested request must fail before GET once its parent's deadline elapsed."""
    import src.tools.web_reader_tool as module
    from backtest.loaders.acquisition import source_deadline
    from src.data_gateway.reads import owned_source

    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        pytest.fail("expired parent deadline dispatched an HTTP request")

    monkeypatch.setattr(module.requests, "get", get)
    parent = source_deadline.set(time.monotonic() - 1)
    owned = owned_source.set(True)
    try:
        response = json.loads(module.read_url("https://example.org/expired"))
        assert response["status"] == "error"
        assert "deadline expired" in response["error"]
        assert calls == []
    finally:
        owned_source.reset(owned)
        source_deadline.reset(parent)


def test_direct_read_url_retains_legacy_http_timeout(
    reader_gateway, monkeypatch
):
    """The 30s transport contract for direct read_url consumers is unchanged."""
    import src.tools.web_reader_tool as module

    observed = []

    def get(url, *, headers, timeout):
        observed.append(timeout)

        class Response:
            status_code = 200
            text = "Title: Direct reader"

        return Response()

    monkeypatch.setattr(module.requests, "get", get)
    result = json.loads(module.read_url("https://example.org/direct"))
    assert result["status"] == "ok"
    assert observed == [30]
