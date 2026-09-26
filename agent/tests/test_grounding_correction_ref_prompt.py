"""Prompt regressions for grounding ref repair."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.context import _SYSTEM_PROMPT
from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit


def test_correction_prompt_requires_literal_refs_and_full_figures_block(tmp_path: Path) -> None:
    ledger = GroundingLedger(run_dir=tmp_path, user_message="Analyze portfolio risk")
    validation = ledger.validate_final_answer("Portfolio value is 1234.5.")

    prompt = ledger.correction_prompt(validation)

    assert "exact literal tool names or exact call ids" in prompt
    assert "Do not append labels, scopes, parentheses or prose to a ref" in prompt
    assert "prefer exact call ids" in prompt
    assert "Return the FULL revised answer" in prompt
    assert "Preserve or rebuild the final figures block on every revision" in prompt
    assert "normalized raw number" in prompt
    assert "not ARS 123.456.789,125" in prompt
    assert "0.9562% | observed | ..." in prompt
    assert "0.9562 and 0.9562% are different" in prompt
    assert "preserve ALL of those refs" in prompt


def test_system_prompt_keeps_percent_unit_in_figures_declaration() -> None:
    assert "0.9562% | observed | ..." in _SYSTEM_PROMPT
    assert "`0.9562` and `0.9562%`" in _SYSTEM_PROMPT
