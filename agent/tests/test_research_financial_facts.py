"""Contract tests for portable financial facts in versioned research content."""

import json

import pytest

from src.research_artifacts.financial_facts import (
    decode_financial_facts,
    encode_financial_facts,
    validate_financial_facts,
)


def example_fact(**overrides):
    fact = {
        "metric": "revenue",
        "period": "2025-FY",
        "period_kind": "duration",
        "scope": "consolidated",
        "definition": "Revenue under reporting GAAP",
        "value": 1998,
        "currency": "USD",
        "unit": "millions",
        "source_url": "https://example.org/filing",
        "source_section": "Consolidated statement of income",
        "source_page": None,
        "verification_status": "verified",
    }
    fact.update(overrides)
    return fact


def example_document(*facts):
    return {"schema_version": "financial-facts/v1",
            "subject": "Example Corporation", "facts": list(facts)}


def test_round_trip_preserves_numeric_fact_and_provenance():
    document = example_document(example_fact())
    assert decode_financial_facts(encode_financial_facts(document)) == document


def test_missing_value_must_not_be_marked_verified():
    with pytest.raises(ValueError, match="require a numeric value"):
        validate_financial_facts(example_document(example_fact(value=None)))


def test_unavailable_never_contains_a_number():
    with pytest.raises(ValueError, match="must have null value"):
        validate_financial_facts(
            example_document(example_fact(verification_status="unavailable")))


def test_unavailable_can_preserve_missing_source():
    fact = example_fact(value=None, verification_status="unavailable",
                        source_url=None, source_section=None,
                        currency=None, unit=None)
    assert validate_financial_facts(example_document(fact))["facts"][0] == fact


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "123"])
def test_invalid_numeric_values_rejected(value):
    with pytest.raises(ValueError, match="finite numeric"):
        validate_financial_facts(example_document(example_fact(value=value)))


def test_verified_requires_units_and_source():
    with pytest.raises(ValueError, match="provenance and units"):
        validate_financial_facts(example_document(example_fact(source_section=None)))


def test_period_kind_is_explicit_for_stocks_and_flows():
    with pytest.raises(ValueError, match="period_kind"):
        validate_financial_facts(example_document(example_fact(period_kind="quarter")))


def test_narrative_research_is_not_misread_as_facts():
    with pytest.raises(ValueError, match="not a financial facts JSON envelope"):
        decode_financial_facts("Research summary: earnings increased.")


def test_unknown_schema_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        decode_financial_facts(json.dumps({"schema_version": "financial-facts/v2"}))


def test_existing_research_store_round_trip(tmp_path):
    from src.research_artifacts.store import ResearchArtifactStore

    store = ResearchArtifactStore(db_path=tmp_path / "research.sqlite")
    try:
        content = encode_financial_facts(example_document(example_fact()))
        saved = store.save(content=content, data_as_of="2026-11-04",
                           subject="Example Corporation", universe="EXAMPLE",
                           verification_status="partial", completeness="partial")
        revision = store.get_revision(saved.revision.revision_id)
        assert revision is not None
        assert decode_financial_facts(revision.content)["facts"][0]["value"] == 1998
        assert revision.verification_status == "partial"
    finally:
        store.close()
