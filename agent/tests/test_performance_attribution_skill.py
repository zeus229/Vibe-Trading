"""Guards for performance-attribution reporting instructions."""

import json

from src.tools.load_skill_tool import LoadSkillTool


def test_first_skill_page_preserves_signed_observed_contributions() -> None:
    payload = json.loads(
        LoadSkillTool().execute(name="performance-attribution", offset=0)
    )

    assert payload["status"] == "ok"
    assert payload["complete"] is False
    text = payload["content"]
    assert "preserve that numeric sign **every time the value is repeated**" in text
    assert "-3.502 pp" in text
    assert "-3.502 puntos porcentuales" in text
    assert "restó 3.502 puntos porcentuales" in text
    assert "a verb does not negate a positive number" in text
    assert "0 - (-3.502)" in text
