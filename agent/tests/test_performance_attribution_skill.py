"""Guards for performance-attribution reporting instructions."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "skills"
    / "performance-attribution"
    / "SKILL.md"
)


def test_performance_attribution_preserves_signed_observed_contributions() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "preserve that numeric sign **every time the value is repeated**" in text
    assert "-3.502 pp" in text
    assert "a verb does not negate a positive number" in text
    assert "0 - (-3.502)" in text
