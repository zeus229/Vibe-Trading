# Structured financial facts in research artifacts (experimental)

This proposal adds a **portable JSON envelope**, not a second financial database.
Existing immutable research artifacts store the serialized envelope in `content`.
No database migration, new endpoint, scheduled job, or country-specific behavior is required.

## Scope

- `financial-facts/v1` is an opt-in schema; existing narrative artifacts remain valid.
- Every fact has a metric, explicit period and `period_kind` (`duration` for income/flows, `instant` for balance-sheet stocks), scope, definition, value, currency/unit, source location, and individual status.
- `verified` means **claimed evidence status**, not that the library verified the external document. Consumers must independently check evidence before promoting numbers.
- `partial` may retain a numeric value with limitations; `unavailable` requires `null`.
- The artifact's revision-level verification status remains independent from each fact.
- No arithmetic, FX conversion, financial restatement, or GAAP equivalence is inferred.

## Example

```json
{
  "schema_version": "financial-facts/v1",
  "subject": "Example Corporation",
  "facts": [
    {
      "metric": "revenue",
      "period": "2025-FY",
      "period_kind": "duration",
      "scope": "consolidated",
      "definition": "Revenue under issuer reporting GAAP",
      "value": 1998,
      "currency": "USD",
      "unit": "millions",
      "source_url": "https://example.org/filing",
      "source_section": "Consolidated statement of income",
      "source_page": null,
      "verification_status": "verified"
    }
  ]
}
```

## Integration with existing upstream APIs

```python
from src.research_artifacts.financial_facts import encode_financial_facts, decode_financial_facts
from src.research_artifacts.store import ResearchArtifactStore

content = encode_financial_facts(document)
store = ResearchArtifactStore(db_path=isolated_db_path)
result = store.save(content=content, subject=document["subject"],
                    universe="EXAMPLE", data_as_of="2026-11-04",
                    verification_status="partial")
restored = decode_financial_facts(store.get_revision(result.revision.revision_id).content)
store.close()
```

The authenticated existing GET endpoints return the original `content`; clients may decode it without any new route. A new revision is explicitly linked using `artifact_id`, and previous revisions remain immutable.

## Before proposing upstream

1. Run isolated unit tests (`agent/tests/test_research_financial_facts.py`) and existing research artifact regressions.
2. Confirm the authenticated API returns an identical envelope after an isolated save/read; never write to production during validation.
3. Evaluate interoperability with other issuers, GAAP regimes and currencies.
4. Decide whether upstream prefers an optional typed field or a standalone reusable schema, without forcing structured output on narrative research.
5. Keep portfolio-specific scoring, ingestion, and scheduling outside this contribution.

This schema is intentionally small and does not establish that extracted facts are economically correct.
