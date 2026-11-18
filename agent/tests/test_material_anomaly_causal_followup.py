"""Regression tests for causal follow-up on material quantitative anomalies."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_system_prompt_requires_material_anomaly_causal_followup() -> None:
    """Material quantitative anomalies must be verified and causally investigated."""
    from src.agent.context import _SYSTEM_PROMPT

    normalized_prompt = " ".join(_SYSTEM_PROMPT.split())

    assert "Investigate material anomalies before explaining them" in normalized_prompt
    assert "independent market-data source" in normalized_prompt
    assert "company/market news" in normalized_prompt
    assert "corporate actions or instrument adjustments" in normalized_prompt
    assert "do not stop at the statistic or assume it is bad data" in normalized_prompt


def test_system_prompt_does_not_authorize_outlier_rewriting() -> None:
    """An extreme observation alone must never authorize history mutation."""
    from src.agent.context import _SYSTEM_PROMPT

    normalized_prompt = " ".join(_SYSTEM_PROMPT.split())

    assert "Do not delete, winsorize, replace" in normalized_prompt
    assert "only when the anomaly materially changes" in normalized_prompt
