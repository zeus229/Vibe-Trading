# Structured financial facts in research artifacts (experimental)

This proposal adds a **portable JSON envelope**, not a second financial database.
Existing immutable research artifacts store the serialized envelope in `content`.
No database migration, new endpoint, scheduled job, or country-specific behavior is required.

## Scope

- `financial-facts/v1` is an opt-in schema; existing narrative artifacts remain valid.
- Every fact has a metric, explicit ISO date boundaries and `period_kind` (`duration` for income/flows, `instant` for balance-sheet stocks), scope, definition, value, currency/unit, source location, and individual status.
- The document contains a nonempty `facts` array and a `sources` catalog with unique IDs and HTTPS URLs; each verified fact links to a catalog entry. Duplicate metric/period/scope/unit identities are rejected.
- A duration uses `period_start` and `period_end`; a balance-sheet instant uses `as_of_date`. The library cannot infer whether a metric should be an instant or a duration from its free-form name.
- `verified` means **claimed evidence status**, not that the library verified the external document. Consumers must independently check evidence before promoting numbers.
- `partial` may retain a numeric value with limitations; `unavailable` requires `null`.
- The artifact's revision-level verification status remains independent from each fact.
- No arithmetic, FX conversion, financial restatement, or GAAP equivalence is inferred.

## Example

```json
{
  "schema_version": "financial-facts/v1",
  "subject": "Example Corporation",
  "sources": [{"id": "filing-2025", "url": "https://example.org/filing"}],
  "facts": [
    {
      "metric": "revenue",
      "period_start": "2025-01-01",
      "period_end": "2025-12-31",
      "period_kind": "duration",
      "scope": "consolidated",
      "definition": "Revenue under issuer reporting GAAP",
      "value": 1998,
      "currency": "USD",
      "unit": "millions",
      "source_id": "filing-2025",
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

## Agent-generated structured artifacts (opt-in)

The agent now exposes `save_financial_research_artifact` alongside the unchanged
`save_research_artifact` narrative tool. For an explicit request to produce
structured issuer financial statements, the agent researches source filings and
passes a **JSON object** (not a serialized string) to the new tool. This tool
calls `encode_financial_facts` before invoking the existing
`ResearchArtifactStore.save`. Invalid schema or provenance stops the write.
The tool also copies the validated `sources` catalog into revision-level
`evidence` as `{source_id, url}` references, preserving any additional
references supplied by the caller. This avoids `evidence_missing=true`
for documents with source URLs without claiming that those URLs were
independently retrieved or authenticated. The revision is still unverified by default, independently of the
`verification_status` recorded on individual facts; tool success never
attests to external document accuracy.

This opt-in route does not change the general POST API or the legacy free-form
research save tool, which remain compatible with narrative research.
The system prompt asks the agent to keep exact dates and source locations,
preserve formulas for derived numbers, and acknowledge missing evidence.
As with any LLM tool decision, the prompt alone does not guarantee that a
particular research session invokes the save tool: the persisted revision
must be observed and decoded in acceptance testing.

### Next validation

- Confirm local tool registry discovery and that `save_financial_research_artifact`
  is offered to the research agent.
- Run `agent/tests/test_financial_research_artifact_tool.py` and existing
  research-artifact regression tests in a detached worktree.
- Verify a simulated agent tool call uses only isolated databases, produces
  decodable `financial-facts/v1`, preserves immutable revisions, and leaves
  narrative saves untouched.
- Then run a **separate** real-agent YPF research trial against safely isolated
  runtime paths, explicitly checking the tool was called and the artifact
  was persisted. A simulated call does not establish real-agent reliability.

## Before proposing upstream

1. Run isolated unit tests (`agent/tests/test_research_financial_facts.py`) and existing research artifact regressions.
2. Confirm the authenticated API returns an identical envelope after an isolated save/read; never write to production during validation.
3. Evaluate interoperability with other issuers, GAAP regimes and currencies.
4. Decide whether upstream prefers an optional typed field or a standalone reusable schema, without forcing structured output on narrative research.
5. Keep portfolio-specific scoring, ingestion, and scheduling outside this contribution.

This schema is intentionally small and does not establish that extracted facts are economically correct.

## Trust boundary

Validation only establishes **internal structural consistency**. The `verified` label is author-supplied and does not attest to source retrieval, document integrity, accounting basis, extraction accuracy, or reconciliation. The source catalog is an internal cross-reference, not a cryptographic binding to the research artifact's external `evidence` array. Consumers must perform their own evidence verification and policy checks before financial promotion. In particular, this schema does not know which free-form metrics are balance-sheet stocks or period flows, and it cannot prove the reported period matches the issuer filing. Never auto-promote based solely on `verification_status`.
