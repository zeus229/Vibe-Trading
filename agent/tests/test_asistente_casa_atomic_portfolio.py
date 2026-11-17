"""Atomic Asistente Casa connector contract: one plugin call per portfolio refresh."""
from types import SimpleNamespace

import pytest

from src.trading import service as trading_service


def test_atomic_read_invokes_single_local_plugin_operation(monkeypatch):
    profile = SimpleNamespace(
        transport="local_plugin", connector="asistente-casa",
        id="asistente-casa-live-readonly",
    )
    monkeypatch.setattr(trading_service, "profile_by_id", lambda _: profile)
    calls = []

    def fake_plugin(profile_arg, operation, overrides):
        calls.append((profile_arg.id, operation, overrides))
        return {
            "account_snapshot": {"status": "ok", "account": {"portfolio_value": 11.0}, "source_capture_id": "live-1", "captured_at": "2026-11-17T19:00:00Z"},
            "positions_snapshot": {"status": "ok", "positions": [{"symbol": "YPFD"}], "source_capture_id": "live-1", "captured_at": "2026-11-17T19:00:00Z"},
            "source_capture_id": "live-1",
            "captured_at": "2026-11-17T19:00:00Z",
        }

    monkeypatch.setattr(trading_service, "_local_plugin_call", fake_plugin)
    monkeypatch.setattr(trading_service, "_with_profile", lambda _profile, payload: payload)
    result = trading_service.get_portfolio_snapshot(
        profile.id, connection_id="connection-1"
    )
    assert calls == [(profile.id, "get_portfolio_snapshot", {"connection_id": "connection-1"})]
    assert result["account"]["account"]["portfolio_value"] == 11.0
    assert result["positions"]["positions"][0]["symbol"] == "YPFD"


def test_atomic_read_rejects_missing_half_without_fallback(monkeypatch):
    profile = SimpleNamespace(
        transport="local_plugin", connector="asistente-casa",
        id="asistente-casa-live-readonly",
    )
    monkeypatch.setattr(trading_service, "profile_by_id", lambda _: profile)
    monkeypatch.setattr(
        trading_service, "_local_plugin_call",
        lambda *_args, **_kwargs: {"account_snapshot": {"status": "ok"}},
    )
    with pytest.raises(ValueError, match="missing account or positions"):
        trading_service.get_portfolio_snapshot(profile.id)


@pytest.mark.parametrize("changed", ["source_capture_id", "captured_at"])
def test_atomic_read_rejects_mismatched_capture_identity(monkeypatch, changed):
    profile = SimpleNamespace(
        transport="local_plugin", connector="asistente-casa",
        id="asistente-casa-live-readonly",
    )
    monkeypatch.setattr(trading_service, "profile_by_id", lambda _: profile)
    account = {"source_capture_id": "capture-a", "captured_at": "2026-11-17T19:00:00Z"}
    positions = dict(account)
    positions[changed] = "capture-b" if changed == "source_capture_id" else "2026-11-17T19:01:00Z"
    monkeypatch.setattr(
        trading_service, "_local_plugin_call",
        lambda *_args, **_kwargs: {
            "account_snapshot": account, "positions_snapshot": positions,
            **account,
        },
    )
    with pytest.raises(ValueError, match="missing or mismatched"):
        trading_service.get_portfolio_snapshot(profile.id)
