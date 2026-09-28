"""Tests for the API server bind default and non-loopback warning.

Covers the secure-by-default behavior added for #333:
  - `_is_loopback_bind_host` classification (IPv4 / IPv6 / hostname / edge)
  - `serve_main` defaults the bind address to loopback (127.0.0.1)
  - binding a non-loopback address without API_AUTH_KEY emits a startup warning,
    while loopback or a configured key stays quiet

Warning assertions match the bind warning's own text rather than the bare
``[warn]`` prefix, so an unrelated startup warning (e.g. a missing frontend
build in CI) cannot satisfy or break them.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import api_server

# Unique substring of the non-loopback bind warning (api_server.py:3513).
_BIND_WARN = "without API_AUTH_KEY set"


@pytest.mark.unit
@pytest.mark.parametrize(
    "host, expected",
    [
        ("127.0.0.1", True),
        ("127.0.0.2", True),
        ("::1", True),
        ("0:0:0:0:0:0:0:1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.5", False),
        ("", False),
    ],
)
def test_is_loopback_bind_host(host: str, expected: bool) -> None:
    assert api_server._is_loopback_bind_host(host) is expected


def _run_serve(argv: list[str]) -> str | None:
    """Invoke serve_main with uvicorn stubbed; return the host it bound to.

    The frontend mount / static-file branches are short-circuited because
    uvicorn.run raises SystemExit before reaching the server loop.
    """
    return _run_serve_capturing(argv)["host"]  # type: ignore[return-value]


def _run_serve_capturing(argv: list[str]) -> dict[str, object]:
    """Invoke serve_main with uvicorn stubbed; return the captured kwargs."""
    captured: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        captured["host"] = kwargs.get("host") or (args[1] if len(args) > 1 else None)
        raise SystemExit(0)

    with mock.patch("uvicorn.run", fake_run):
        try:
            api_server.serve_main(argv)
        except SystemExit:
            pass
    return captured


@pytest.mark.unit
def test_serve_defaults_to_loopback() -> None:
    assert _run_serve([]) == "127.0.0.1"


@pytest.mark.unit
def test_serve_honors_explicit_host() -> None:
    assert _run_serve(["--host", "0.0.0.0"]) == "0.0.0.0"


@pytest.mark.unit
def test_non_loopback_without_key_warns(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", None, raising=False)

    _run_serve(["--host", "0.0.0.0"])

    out = capsys.readouterr().out
    assert _BIND_WARN in out


@pytest.mark.unit
def test_loopback_does_not_warn(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", None, raising=False)

    _run_serve(["--host", "127.0.0.1"])

    out = capsys.readouterr().out
    assert _BIND_WARN not in out


@pytest.mark.unit
def test_non_loopback_with_key_does_not_warn(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("API_AUTH_KEY", "secret")
    monkeypatch.setattr(api_server, "_API_KEY", "secret", raising=False)

    _run_serve(["--host", "0.0.0.0"])

    out = capsys.readouterr().out
    assert _BIND_WARN not in out


@pytest.mark.unit
def test_serve_mounts_frontend_when_routes_include_router_without_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_api_file = tmp_path / "agent" / "api_server.py"
    fake_dist = tmp_path / "frontend" / "dist"
    fake_dist.mkdir(parents=True)
    fake_api_file.parent.mkdir(parents=True)
    fake_api_file.write_text("# test module path\n", encoding="utf-8")
    (fake_dist / "index.html").write_text("<div>Vibe-Trading</div>\n", encoding="utf-8")

    monkeypatch.setattr(api_server, "__file__", str(fake_api_file))
    api_server.app.routes.insert(0, SimpleNamespace())
    try:
        assert _run_serve([]) == "127.0.0.1"
    finally:
        api_server.app.routes.pop(0)


# ---------------------------------------------------------------------------
# forwarded_allow_ips (VIBE_TRADING_FORWARDED_ALLOW_IPS)
#
# Covers the reverse-proxy trust fix: a proxy terminating TLS on a host other
# than 127.0.0.1 (e.g. Cloudflare Tunnel) needs its IP passed to Uvicorn's
# forwarded_allow_ips, or X-Forwarded-Proto is silently ignored and
# request.url.scheme stays "http" behind HTTPS.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_forwarded_allow_ips_defaults_to_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config.accessor import reset_env_config

    monkeypatch.delenv("VIBE_TRADING_FORWARDED_ALLOW_IPS", raising=False)
    reset_env_config()
    try:
        captured = _run_serve_capturing([])
    finally:
        reset_env_config()

    assert captured["forwarded_allow_ips"] == "127.0.0.1"
    assert captured["proxy_headers"] is True


@pytest.mark.unit
def test_forwarded_allow_ips_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.config.accessor import reset_env_config

    monkeypatch.setenv("VIBE_TRADING_FORWARDED_ALLOW_IPS", "192.168.0.100")
    reset_env_config()
    try:
        captured = _run_serve_capturing([])
    finally:
        reset_env_config()

    assert captured["forwarded_allow_ips"] == "192.168.0.100"


@pytest.mark.unit
def test_forwarded_allow_ips_reaches_uvicorn_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The configured value must be the exact kwarg uvicorn.run receives."""
    from src.config.accessor import reset_env_config

    monkeypatch.setenv("VIBE_TRADING_FORWARDED_ALLOW_IPS", "192.168.0.100,10.0.0.5")
    reset_env_config()
    try:
        captured = _run_serve_capturing([])
    finally:
        reset_env_config()

    assert captured["forwarded_allow_ips"] == "192.168.0.100,10.0.0.5"
