"""The strategy-generate skill must tell the agent how to declare every BYMA tick table."""

from pathlib import Path

from backtest.engines.argentina_equity import _TICK_TABLES

SKILL = Path(__file__).resolve().parents[1] / "src/skills/strategy-generate/SKILL.md"


def test_skill_names_every_tick_table_and_its_boundary_dates():
    text = SKILL.read_text(encoding="utf-8")
    for table in _TICK_TABLES:
        assert f'"{table}"' in text
    for boundary in ("2024-07-04", "2025-09-08"):
        assert boundary in text
    assert "never ask the user for the parameter name" in text
