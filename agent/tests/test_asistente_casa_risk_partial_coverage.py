import pandas as pd
import pytest

from src.tools.asistente_casa_portfolio_risk_tool import _select_risk_panel


def _panel():
    dates = pd.date_range("2026-01-01", periods=84, freq="B")
    return pd.DataFrame(
        {
            "AAA": range(100, 184),
            "BBB": range(200, 284),
            "JPM": [None] * 79 + list(range(300, 305)),
        },
        index=dates,
        dtype=float,
    )


def test_partial_fallback_excludes_short_history_symbol_when_value_coverage_is_high():
    closes, weights, meta = _select_risk_panel(
        _panel(),
        {"AAA": 0.50, "BBB": 0.426749, "JPM": 0.073251},
    )

    assert len(closes) == 84
    assert meta["coverage_mode"] == "partial_high_coverage"
    assert meta["excluded_symbols"] == ["JPM"]
    assert meta["included_symbols"] == ["AAA", "BBB"]
    assert meta["included_weight_pct"] == pytest.approx(92.6749, abs=1e-4)
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["AAA"] == pytest.approx(0.50 / 0.926749)


def test_partial_fallback_fails_closed_when_excluded_weight_is_too_large():
    with pytest.raises(ValueError, match="insufficient common historical coverage"):
        _select_risk_panel(
            _panel(),
            {"AAA": 0.45, "BBB": 0.40, "JPM": 0.15},
        )


def test_strict_full_basket_remains_primary_path():
    dates = pd.date_range("2026-01-01", periods=40, freq="B")
    panel = pd.DataFrame(
        {"AAA": range(40), "BBB": range(100, 140)},
        index=dates,
        dtype=float,
    )

    closes, weights, meta = _select_risk_panel(panel, {"AAA": 0.6, "BBB": 0.4})

    assert len(closes) == 40
    assert meta["coverage_mode"] == "strict_full_basket"
    assert meta["excluded_symbols"] == []
    assert weights == {"AAA": 0.6, "BBB": 0.4}


def test_partial_fallback_can_exclude_zero_history_symbol_when_weight_is_small():
    dates = pd.date_range("2026-01-01", periods=40, freq="B")
    panel = pd.DataFrame(
        {
            "AAA": range(40),
            "BBB": range(100, 140),
            "NEW": [None] * 40,
        },
        index=dates,
        dtype=float,
    )

    closes, weights, meta = _select_risk_panel(
        panel,
        {"AAA": 0.55, "BBB": 0.40, "NEW": 0.05},
    )

    assert len(closes) == 40
    assert meta["coverage_mode"] == "partial_high_coverage"
    assert meta["excluded_symbols"] == ["NEW"]
    assert meta["included_weight_pct"] == pytest.approx(95.0)
    assert sum(weights.values()) == pytest.approx(1.0)
