"""Regression tests for causal follow-up on material quantitative anomalies."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_system_prompt_requires_material_anomaly_causal_followup() -> None:
    """Material quantitative anomalies must be verified and causally investigated."""
    from src.agent.context import _SYSTEM_PROMPT

    assert "Investigate material anomalies before explaining them" in _SYSTEM_PROMPT
    assert "independent market-data source" in _SYSTEM_PROMPT
    assert "company/market news" in _SYSTEM_PROMPT
    assert "corporate actions or instrument adjustments" in _SYSTEM_PROMPT
    assert "do not stop at the statistic or assume it is bad data" in _SYSTEM_PROMPT


def test_system_prompt_does_not_authorize_outlier_rewriting() -> None:
    """An extreme observation alone must never authorize history mutation."""
    from src.agent.context import _SYSTEM_PROMPT

    assert "Do not delete, winsorize, replace" in _SYSTEM_PROMPT
    assert "only when the anomaly materially changes" in _SYSTEM_PROMPT
