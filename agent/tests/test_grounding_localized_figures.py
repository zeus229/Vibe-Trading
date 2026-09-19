"""Localized number regressions for figures declarations."""

from __future__ import annotations

from src.agent.grounding.figures import parse_figures_block


def test_ars_period_grouping_with_decimal_comma_parses_as_one_declaration() -> None:
    block = parse_figures_block(
        """answer

```figures
ARS 210.065.669,185 | observed | meta.total_value_ars | asistente_casa_portfolio_risk_xray
```
"""
    )

    assert block.malformed == ()
    assert len(block.declarations) == 1
    declaration = block.declarations[0]
    assert declaration.value == 210065669.185
    assert declaration.value_text == "210065669.185"
    assert declaration.role == "observed"


def test_normalized_ars_value_still_parses() -> None:
    block = parse_figures_block(
        """answer

```figures
210065669.185 | observed | meta.total_value_ars | asistente_casa_portfolio_risk_xray
```
"""
    )

    assert block.malformed == ()
    assert block.declarations[0].value == 210065669.185
