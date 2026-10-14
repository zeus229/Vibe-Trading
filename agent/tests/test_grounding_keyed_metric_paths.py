"""Symbol-keyed analysis metric paths are classified without guessing generic maps."""
from __future__ import annotations

import pytest

from src.agent.grounding.evidence import _metric_kind_for_path


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("risk_contribution_pct.AAPL.US", "contribution"),
        ("risk_contribution_pct.YPFD", "contribution"),
        ("contribution.MSFT.US", "contribution"),
        ("correlation_matrix.AAPL.US.MSFT.US", "correlation"),
        ("correlation_matrix.YPFD.PAMP", "correlation"),
        ("other_data.AAPL.US", None),
        ("unrelated_map.YPFD", None),
        ("risk_contribution_pct.unknown", None),
    ],
)
def test_symbol_keyed_metric_paths(path: str, expected: str | None) -> None:
    assert _metric_kind_for_path(path) == expected
