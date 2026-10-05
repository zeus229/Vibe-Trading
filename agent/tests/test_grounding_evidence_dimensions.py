"""Regression tests for generic evidence dimension inheritance."""

import pytest

from src.agent.grounding.evidence import _leaf_dimension, _name_tokens


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("data.composition.by_sector_pct.Energy", "ratio"),
        ("data.composition.by_sector_pct.Energía", "ratio"),
        ("data.composition.by_country_pct.Argentina", "ratio"),
        ("data.composition.weights.Some Company", "ratio"),
        ("data.composition.by_sector_pct.trade_count", "count"),
        ("data.composition.by_sector_pct.return_observations", "ratio"),
        ("data.composition.by_sector_pct.cash_amount", None),
        ("data.composition.by_sector_pct.price_ars", None),
    ],
)
def test_leaf_dimension_inherits_only_for_opaque_mapping_labels(path, expected):
    assert _leaf_dimension(path) == expected


def test_name_tokens_preserve_unicode_words():
    assert _name_tokens("Fondo de Inversión") == ["fondo", "de", "inversión"]
    assert _name_tokens("Japón") == ["japón"]
