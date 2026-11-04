"""Agent-facing structured financial research artifact tool regression tests."""

import json

from src.research_artifacts.financial_facts import decode_financial_facts
from src.research_artifacts.store import ResearchArtifactStore
from src.tools.financial_research_artifact_tool import SaveFinancialResearchArtifactTool
from src.tools.research_artifact_tool import SaveResearchArtifactTool


def _document(value=100):
    return {
        "schema_version": "financial-facts/v1",
        "subject": "Example Corporation",
        "sources": [{"id": "annual", "url": "https://example.org/annual"}],
        "facts": [{
            "metric": "revenue",
            "period_kind": "duration",
            "period_start": "2025-01-01",
            "period_end": "2025-12-31",
            "scope": "consolidated",
            "definition": "Reported consolidated revenue",
            "value": value,
            "currency": "USD",
            "unit": "millions",
            "source_id": "annual",
            "source_section": "Income statement",
            "verification_status": "partial",
        }],
    }


def test_financial_tool_validates_before_opening_store(tmp_path, monkeypatch):
    from src.tools import financial_research_artifact_tool as tool_module

    def unexpected_store():
        raise AssertionError("must not open a database for invalid input")

    monkeypatch.setattr(tool_module, "ResearchArtifactStore", unexpected_store)
    tool = SaveFinancialResearchArtifactTool()
    document = _document()
    document["facts"][0]["value"] = float("nan")
    result = json.loads(tool.execute(document=document, data_as_of="2026-11-04"))
    assert result["status"] == "error"
    assert "finite numeric" in result["error"]


def test_financial_tool_round_trip_idempotence_and_revisions(tmp_path, monkeypatch):
    from src.tools import financial_research_artifact_tool as tool_module

    path = tmp_path / "isolated.sqlite"
    monkeypatch.setattr(tool_module, "ResearchArtifactStore",
                        lambda: ResearchArtifactStore(db_path=path))
    tool = SaveFinancialResearchArtifactTool()
    first = json.loads(tool.execute(document=_document(), data_as_of="2026-11-04"))
    assert first["status"] == "ok"
    assert first["created"] is True
    revision = first["revision"]
    artifact_id = revision["artifact_id"]

    again = json.loads(tool.execute(document=_document(), data_as_of="2026-11-04",
                                    artifact_id=artifact_id))
    assert again["status"] == "ok"
    assert again["created"] is False
    assert again["revision"]["revision_id"] == revision["revision_id"]

    revised = json.loads(tool.execute(document=_document(105), data_as_of="2026-11-04",
                                      artifact_id=artifact_id))
    assert revised["status"] == "ok"
    assert revised["created"] is True
    assert revised["revision"]["revision_number"] == 2

    store = ResearchArtifactStore(db_path=path)
    try:
        old = store.get_revision(revision["revision_id"])
        new = store.get_revision(revised["revision"]["revision_id"])
        assert decode_financial_facts(old.content)["facts"][0]["value"] == 100
        assert decode_financial_facts(new.content)["facts"][0]["value"] == 105
        assert new.verification_status == "unverified"
    finally:
        store.close()


def test_narrative_research_tool_stays_unchanged(tmp_path, monkeypatch):
    from src.tools import research_artifact_tool as tool_module

    path = tmp_path / "narrative.sqlite"
    monkeypatch.setattr(tool_module, "ResearchArtifactStore",
                        lambda: ResearchArtifactStore(db_path=path))
    result = json.loads(SaveResearchArtifactTool().execute(
        subject="Example Corporation", content="Narrative findings.",
        data_as_of="2026-11-04"))
    assert result["status"] == "ok"
    store = ResearchArtifactStore(db_path=path)
    try:
        revision = store.get_revision(result["revision"]["revision_id"])
        assert revision.content == "Narrative findings."
    finally:
        store.close()


def test_financial_tool_requires_document_object(tmp_path, monkeypatch):
    from src.tools import financial_research_artifact_tool as tool_module

    def unexpected_store():
        raise AssertionError("invalid document must never reach storage")

    monkeypatch.setattr(tool_module, "ResearchArtifactStore", unexpected_store)
    tool = SaveFinancialResearchArtifactTool()
    for bad in [None, '{"schema_version":"financial-facts/v1"}']:
        result = json.loads(tool.execute(document=bad, data_as_of="2026-11-04"))
        assert result["status"] == "error"
