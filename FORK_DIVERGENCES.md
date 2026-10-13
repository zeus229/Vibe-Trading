# Fork divergences from HKUDS/Vibe-Trading

Last upstream sync: 2026-10-10 (upstream `8e430079`, merged onto fork `bfc7a647`).

## Absorbed by upstream in the 2026-10-10 sync (do not re-add)

Upstream merged our PRs #1767, #1768 and #1769 (as `c87fb83e`, `a0208f17`, `5298755e`):

- `portfolio_summary(snapshot_id=...)` + `read_identity` (mode, snapshot_id, as_of, valuation_version),
  fail-closed `snapshot_not_found`, blank id fails closed, stored snapshot hidden when its source is
  disabled. The fork's extra `configuration_fingerprint` was dropped to match upstream.
- `technical_indicators(end_date=...)` strict boundary + `read_identity.dataset_fingerprint`.
- Code-tool envelope bookkeeping (`exit_code`, `elapsed_seconds`...) is not grounding evidence
  (`_ENVELOPE_FIELDS`).
- Upstream also moved `_microcompact` into `agent/context_compact.py` and the identity/figures-block
  checks into the grounding registry (`identity_checks.py`, `figures_checks.py`).

Still fork-only on top of those: `portfolio_summary` `view=compact` and `no_cache`; the fork's
`technical_indicators` fingerprint also hashes the provider provenance and reports `requested_start_date`/
`latest_date`, plus `no_cache`; `preserve_during_microcompact` skip in `context_compact._microcompact`
(`preserve_tools`) and the replay visibility lease.

## Codex tools sent with `strict: false` (absorbed by upstream #1785 on 2026-10-11)

`_convert_tools` in `agent/src/providers/openai_codex.py` sends `"strict": False` on every converted tool.
Upstream (`8e430079`) omits the key. **Temporary fork correction; remove it once upstream carries the same fix.**

- **Problem:** with `strict` absent, the Codex Responses endpoint (`chatgpt.com/backend-api/codex/responses`) behaves as
  strict. The model emits every schema property, using `""` for an optional string, so optional arguments can never be
  omitted. `portfolio_summary` rejects a blank `snapshot_id` (`snapshot_not_found`, upstream #1769, which we keep
  fail-closed) and the agent ended in `no_progress` after 8 retries with placeholders (session `ce36ca6e896b`).
- **Reproduction (model `gpt-5.6-sol`, one request per variant, synthetic read-only tool):** without `strict` the model
  generated `{"snapshot_id": "", "view": "extended", "no_cache": false}`; with `"strict": false` it generated `{}`.
- **End-to-end validation (isolated candidate, copied data):** original prompt succeeded in 2 calls: `portfolio_summary`
  latest without `snapshot_id`, then pinned to the returned id; same snapshot, `as_of`, 38 positions and totals.
- **Not needed for the fix:** the `snapshot_id` description and `snapshot_not_found` `hint` patch was evaluated and did
  not change the model's behaviour (8/8 blank or placeholder ids), so it is not part of the fork.
- **Tests:** `tests/test_openai_codex_tool_schema.py`.
- **Upstream:** branch `fix/codex-tools-non-strict` (commit `6e4b5e0`, DCO signed-off, based on upstream `8e430079`) is
  prepared on the fork. **The PR is NOT published yet.** `src/providers/` is a protected area upstream, so an issue or prior
  discussion may be needed first.
- **Fork commit:** `46cb5432` (cherry-pick of `6e4b5e0`), deployed as image `prod-46cb5432`; rollback `prod-c5cbca64`.

## Grounding: derived-formula notes are fail-closed

`_formula_in_note` (`agent/src/agent/grounding/policies.py`) treats an ASCII colon as the
separator between descriptive labels and exactly one complete arithmetic expression.
Anything else after the colon (a stated result `= 1.25`, a trailing explanation,
units, a second formula) makes the note unextractable, so the derived figure is not
preserved and the model must repair it.

Upstream #1728 tolerates trailing results, explanations and units after the colon.
We keep the stricter rule on purpose: a derived figure is only accepted when the whole
suffix is machine arithmetic. The full-width colon keeps the tolerant upstream reading.

Tests: `tests/test_grounding_note_formula_extraction.py` (fork policy) and
`tests/test_grounding_formula_note.py` (upstream cases; the three tolerant ASCII-colon
cases are asserted to return `None`). If this policy is ever relaxed, change both.

## Argentina dashboard link

`frontend/src/components/layout/Layout.tsx` renders an "Argentina" link under
`SidebarNavigation` (`VITE_ASISTENTE_CASA_UI_URL`).

## Rounding band stays at 0.5%

`figures.ROUNDED_BAND = 0.005` is deliberate. A coarse rendering such as `0.82467 -> 0.82`
is rejected; the answer must keep more digits (`0.825`). The older fork branch that widened
the band to 1% was superseded by this policy and archived.

## Fork-only features that must remain

- **Asistente Casa integration:** `asistente_casa_portfolio_risk_xray` (canonical weights and
  persisted EOD history from Asistente Casa, strict shared-calendar alignment with an explicit
  high-coverage fallback). `lookback_sessions=N` (merged from PR #23) selects the last N shared
  sessions after alignment, widens the fetch once to `earliest_common_date`, and fails closed
  when fewer than N sessions exist.
- **PPI conversation tools** (`calculate_ppi_indicators`, conversation-scoped PPI MCP research),
  with `PPI_TRADING_DISABLED=True` enforced by deployment and all operations read-only.
- **Compact portfolio snapshot** with canonical daily change, ISO-4217 native currencies and
  unsettled-cash modelling.
- **Grounding contract:** repair/correction contract, compact repair queue for >24 issues,
  preserve-options and derived-formula preservation, fail-closed ASCII-colon formulas.
- **Argentina support:** Yahoo `.BA` routing, Spanish decimal-comma/grouped figures, dashboard link.
- **CI:** Windows desktop workflow narrowed to real build inputs (PR #16).

## Read identity and freshness (fork additions)

- (Historical: absorbed by upstream 2026-10-10, see top.) `portfolio_summary(snapshot_id=...)` reads one immutable stored snapshot (both `extended` and
  `compact` views) and returns `snapshot_id` plus `read_identity` (mode, as_of, configuration
  fingerprint). A missing or incompatible id fails closed with `snapshot_not_found`; omitting it
  keeps the latest-snapshot behaviour.
- `technical_indicators(end_date=YYYY-MM-DD)` pins the acquisition boundary (strict,
  zero-padded format) and returns `read_identity` with a SHA-256 `dataset_fingerprint` of the exact
  bars and provenance used.
- Replay visibility lease: a result restored after compaction is protected from microcompaction
  until one model request carried it; a write invalidation clears pending leases.

## Experimental branches kept (not in `main`)

| Branch | Unique value | Why not merged |
|---|---|---|
| `exp/ac-native-equities-risk-xray-20260919` | native-equities risk X-Ray pilot with `.BA` history routing and pilot document | experimental; worktree `vibe-native-equity-pilot-20260919` |

Retired branches are archived as tags `archive/<date>/<branch>` on the fork.

## Audit 2026-10-09: grounding, Research Goal and MCP against upstream `b1f6ce71`

Since the merge base, upstream changed only three grounding files in small ways
(`ledger._passed_figures`, the clean-figures keep list in `release.py`, and ASCII-colon
handling in `policies._formula_in_note`). Everything else under `agent/src/agent/grounding/`
is fork-only. A behaviour-level comparison found no parallel implementation of any upstream
feature to remove. The only demonstrable redundancy was the ASCII `:` entries in the two
separator tuples of `_formula_in_note`: a note containing `:` returns earlier through the strict
colon branch, so those entries were unreachable and were removed (no behaviour change; 1,991
focused grounding/Goal/MCP/Asistente Casa tests give identical results before and after).

Kept on purpose, with the reason:

| Area | Why it stays |
|---|---|
| `derived.py`, `repair_contract.py` | Not in upstream: proven derived-formula preservation and the post-failure repair/correction contract. |
| `release.py` compact repair queue (>24 issues) and "preserve clean figures" wording | Upstream's keep list is merged into it; the compact queue is stricter (every rejected issue is shown). |
| `policies.py` / `evidence.py` / `figures.py` extensions | Exact refs and indexed field refs, derived aggregates, locale/grouped numbers, per-item identity: fork-only and covered by ~1,800 tests. |
| `ROUNDED_BAND = 0.005` and fail-closed ASCII-colon formulas | Deliberate policy (see above). |
| `goal_tool.py` `completion_contract` / `criterion_index` | Fork-only repair aid for completion audits; upstream #1688 only covers tool-call provenance. |
| `tools/mcp.py` PPI conversation scoping, Asistente Casa tools | Fork-only integrations. |
| `api_server.py` `proxy_headers` + `forwarded_allow_ips` | Required behind Cloudflare/Authentik; its loss caused browser 403s on 2026-10-09 (guarded by `tests/test_serve_bind.py`). |

## Upstream contribution bundle (historical, completed 2026-10-10)

Both capabilities were contributed and merged upstream as #1768 (`technical_indicators`
`end_date` + `read_identity.dataset_fingerprint`) and #1769 (`portfolio_summary(snapshot_id=...)`
+ `read_identity`), together with #1767 (code-tool envelope is not grounding evidence). The
`contrib/*-20261009` branches were archived as tags and deleted. Nothing here is pending.

**Grounding follow-up:** upstream roadmap issue
https://github.com/HKUDS/Vibe-Trading/issues/1622 tracks policy registry and
check observability. Prior contributions include #1702 (keep verified figures
through correction), #1728 (labelled formulas), #1638 (exact reference repair),
#1688 (Research Goal provenance), plus locale and timestamp corrections.
Do not re-propose replay lease (#1635); maintain the deliberate ASCII-colon
fail-closed and 0.5% rounding policies documented above. The remaining
`feat/asistente-casa-provider-identity` branch needs a clean port of
ISIN-based .BA identity, allowed backtest evidence, and compaction evidence
preservation before considering any generic upstream PR. Check open #1691
(registry work) for overlap first. Only focused tests; no deployment for
upstream PR preparation.

## Post-sync audit 2026-10-11

Compared fork `7bf92efd` with upstream `8e430079`. Removed as redundant (behaviour-identical):
the fork's own `end_date` parser in `technical_indicators` (upstream's `date.fromisoformat`
round-trip is equivalent and covered by the same negative tests), the duplicated
`analysis_context` snapshot selection and `read_mode` variable in `portfolio/service.py`, an unused
`hashlib` import, and ~100 lines of test classes duplicated by the merge in
`tests/test_technical_indicator_tool.py`.

Kept after comparison (not duplicates):

| Item | Why it stays |
|---|---|
| `technical_indicators` fingerprint hashes provider provenance and `isoformat` bar labels; `read_identity` has `requested_start_date`/`latest_date`/`provenance` | Different value from upstream's bars-only fingerprint. Already served by prod images since 2026-10-09, so the format is not changed without a consumer audit (none found in Asistente Casa). |
| `no_cache` on `technical_indicators` and `portfolio_summary` | Freshness escape hatch for replay after compaction; upstream has no equivalent. |
| `portfolio_summary(view="compact")` and `_missing_snapshot` | Fork-only canonical ARS view and its error envelope. |
| `ledger.py` skips generic flattening for `backtest` entirely | Stricter than upstream `_ENVELOPE_FIELDS` (which only drops top-level bookkeeping keys); both are needed. |
| `_microcompact(preserve_tools=...)`, `preserve_during_microcompact`, replay visibility lease | Upstream's `context_compact.py` has only `preserve_tool_call_ids`. |
| ISIN-verified `.BA`, repair contract, 0.5 % band, ASCII-colon formulas, proxy/forwarded IPs | Deliberate fork policy; see sections above. |

Possible future upstream PRs (not opened): the `preserve_during_microcompact` opt-in and
`replay_after_compaction` interplay; check upstream #1622/#1691 first.

## Provider identity, backtest evidence and microcompact (recovered 2026-10-09)

Recovered from `feat/asistente-casa-provider-identity` as a clean port (archived tag
`archive/20261009/feat/asistente-casa-provider-identity`):

- **ISIN-verified `.BA` identity.** `portfolio_summary` rows from the `asistente-casa` connector carry
  `provider_identity` / `underlying`; the grounding ledger locks a `.BA` symbol only when the provider is
  Yahoo, resolution is `isin`, `verified` is true and `resolved_by_isin` equals the row ISIN. Other
  connectors cannot smuggle an identity (normalization nulls it). A locked `.BA` identity must be consumed
  with the exact qualified symbol; a bare ticker is not accepted for it.
- **Backtest envelope is not evidence.** A backtest's `exit_code`, `elapsed_seconds` and raw stdout no
  longer mint "observed" provenance through the generic numeric flattening (`ledger.ingest_tool_result`).
- **Microcompact keeps compact evidence.** Tools with `preserve_during_microcompact`
  (`portfolio_summary`, `technical_indicators`, `financial_rigor`, `backtest`) are skipped by layer-1
  microcompact, alongside the replay lease.

Deliberately discarded: admitting labelled-JSON stdout metrics of a backtest as evidence (numbers printed by
model-authored code are unverifiable; use `technical_indicators`), the extra `vol20_ann`/`rsi14`/`macd`
analysis aliases, and reading `2,639499655314571` as one number (conflicts with the current locale policy).
Already absorbed in `main`: `.BA` regexes, venue and currency, Yahoo routing, precision-aware matching and
inline formulas.

Upstream-generic candidates: the microcompact opt-in attribute (`preserve_during_microcompact`) and the rule
that a code-execution tool's envelope bookkeeping must not become grounding evidence. The ISIN identity is
Asistente Casa specific and stays in the fork.

## Follow-up backlog after cleanup (2026-10-10)

Production at the end of the cleanup: fork `c5cbca643e1167744676275eadf262402c84276b`,
image `vibe-trading:prod-c5cbca64` (production verification and container changes are
outside this documentation commit). Upstream baseline `8e430079`.

**Removed in the cleanup:** duplicate local `technical_indicators.end_date` parser,
duplicate `analysis_context` snapshot selection/`read_mode`, unused portfolio
`hashlib` import, and approximately 100 lines of tests duplicated by the upstream
merge. PRs #1767, #1768, and #1769 are merged into upstream; their old contribution
branches were archived and deleted. No parallel reimplementation should be added.

**Still deliberate fork behavior:** provenance-inclusive indicators fingerprint
and extra read identity fields; `no_cache`; portfolio compact view; stricter
backtest evidence exclusion; microcompact preservation plus replay lease;
ISIN-verified Argentina listings; grounding repair contract, 0.5% rounding
and fail-closed labelled formulas; PPI/Asistente Casa read-only integration;
reverse-proxy forwarding configuration. See the post-sync table above for detail.

### Future evaluation: adopt upstream indicator fingerprint if equivalent

Upstream #1768 hashes the interval and observed bars (date, close, volume),
while the fork additionally hashes provenance and serializes date labels with
`isoformat`. The two hash values are **not interchangeable** merely because
the same bars were requested. Do not silently change the fingerprint contract.

In a future isolated worktree, inventory all consumers (tool responses, research
prompts, persisted records, replay/cache, Asistente Casa, tests). Compare both
implementations with equal bars/provider, changed provenance but identical bars,
timezone/index variations, missing/short volume, and changed bars. Explicitly
decide whether provenance belongs only in `read_identity.provenance` while
`dataset_fingerprint` matches upstream. Check whether extra read-identity fields
are needed. If consumers and regressions demonstrate equivalence, remove the
fork-specific hashing code, adopt upstream, and update golden tests/docs.
Otherwise preserve and document the intentional differing identity. Do not
combine this evaluation with a production update.

### Grounding issue #1622: maintainer guidance pending

Our [derived-operand coverage question](https://github.com/HKUDS/Vibe-Trading/issues/1622#issuecomment-6044292409)
asks how to distinguish unsupported financial operands from legitimate scalar
constants in formulas. There was no maintainer reply to that particular comment
at the time of this note. Do not write a new behavioral grounding PR until
the intended contract is clarified. A short, courteous follow-up in the issue
is appropriate, without assuming a response deadline.

## Audit 2026-10-13 — upstream `300c6aa6` / fork `1d906e68`

- Upstream #1785 already includes the Codex optional-arguments fix. The fork and upstream at the cited SHAs have **identical blob SHA `9826a3cfea8991dfe2c96f373d814fa120274e2b`** for `agent/src/providers/openai_codex.py`. Keep `strict: false` as **native upstream behavior**, not a local patch; no code deletion is warranted.
- Upstream #1622 added derived data-operand evidence checks and explicit declared parameter requirements (2026-10-11 changelog). The fork still has `agent/src/agent/grounding/derived.py`, absent at upstream `300c6aa6`; **do not delete it** until differential contract tests establish equivalence for provenance, mixed scope, unavailable operands, and coefficient labels.
- `context_compact.py` has different blobs (fork `a836e07dcf8606b41e1b8751427bea924e9ca6a5`, upstream `9ee81bc045ded3835c11d2782fd658c282121523`); fork adds `preserve_during_microcompact`. Upstream's existing preserved call IDs are not by themselves proof of equivalent replay visibility. Keep this behavior pending a focused replay/microcompact trace.
- Existing #1767/#1768/#1769 upstream absorption and the 2026-10-11 duplicate-code cleanup remain valid. Do not re-implement those capabilities.
- Scope excluded here: Risk ACCIONES incident and fork PR #71. Trading remains disabled; all Asistente Casa integrations remain read-only.

**Decision:** confirmed redundancy at this point is documentation/classification of the Codex patch, *not an executable duplicate* to remove. Do not create a code branch solely for audit or launch a broad test suite. Before a functional cleanup, compare the current native/fork grounding correction, reference, derived, and replay contracts using paired fixtures, and create one focused candidate only where a behavior-identical replacement is proven.

## 2026-10-13 — ISIN upstream audit and generic contribution candidate

Audited upstream `HKUDS/Vibe-Trading@300c6aa6` directly:

- `agent/src/entities/models.py`: `instrument_id` explicitly accepts stable identifiers including ISIN.
- `agent/src/portfolio/extraetf.py`: parses/validates ISIN identifiers and emits `instrument_id_kind=isin`, with checksum metadata; does **not** establish tradable listing/venue.
- `agent/src/portfolio/service.py`: groups exported positions using an `ISIN:<id>`-style key and explicitly excludes unresolved ISIN/venue identities from price-series risk tools (`_canonical_symbol` and warning path). The upstream already implements the *fail-closed unresolved listing* half of our intended semantics.
- `agent/src/trading/connectors/trading212/sdk.py` extracts provider ISIN; Brazil `br_data/store.py` stores ISIN.
- Upstream `agent/src/agent/grounding/identity.py` does not yet contain the fork's `record_portfolio_...` ISIN-verification path: our fork locks a .BA symbol only when authoritative provider metadata says `resolution=isin`, `verified=true`, `resolved_by_isin` equals holding ISIN, and the provider is Yahoo. Fork `portfolio/service.py` carries that verified mapping in `provider_identity`.
- Therefore **ISIN awareness is native; evidence-backed ISIN -> market-qualified quote identity is still a fork addition**. Do **not** frame an upstream proposal as "add ISIN support". Potential generic contribution: a provider-neutral *verified listing resolution* contract, with source, observed ISIN, resolved ISIN, market, qualified quote symbol, verification status, and fail-closed ambiguity, integrated with upstream's existing identity ledger and extraETF unresolved-venue rule.
- Proposed differential fixtures before any PR: same ISIN listed in two venues, same ticker different instruments, missing market, provider claims wrong ISIN, unverified persisted mapping, verified single-market resolution, CEDEAR/underlying distinct ISIN, mixed broker/import holdings. Ensure no brokerage/trading capability changes. Avoid embedding `asistente-casa`, PPI or `.BA` into generic implementation. Discuss scope first in upstream issue #1171/#1170 or the corresponding identity tracker to avoid duplicate surface.
- Status: **investigate / candidate for future upstream PR**. No generic upstream PR published. Preserve local ISIN verified lock until equivalent cross-provider behavior is shipped and proved.

Other fork-vs-upstream priorities: native Codex optional schema is already identical (no removal); grounding `derived.py` remains fork-only while upstream #1622 has partially overlapping scalar/operand checks (requires paired fixture comparison); microcompact opt-in and replay lease remain distinct (do not delete until visibility equivalence verified); native snapshot/end_date and code envelope #1767-#1769 remain absorbed. Keep Risk ACCIONES / fork PR #71 out of scope.

## Grounding contract comparison (2026-10-13) — code-level gate

Compared upstream `300c6aa6989fcf2ec1cb052c89237b48fabdb524` and fork `1d906e68d1c2fa89634df5d68e0de6845ee9ea2c` by fetched GitHub blobs, not commit-count heuristics.

| Component | Upstream blob | Fork blob | Evidence / disposition |
| --- | --- | --- | --- |
| `grounding/derived.py` | absent | `333eef08e149e89cad1c41f19c9d3f8247078351` | Fork's structural `validate_operands` and coefficient proof are not removable as a module solely because native `policies.py` now constrains financial operands. **Keep pending equivalent test results.** |
| `grounding/repair_contract.py` | absent | `12314b69a76b33a3ea9384b4269b4316fecf3667` | Native correction rules do not establish the fork's `CorrectionContract` retention contract. **Keep.** |
| `grounding/policies.py` | `5a6cb0e9d154f0c033ba5548bffac4ad3879f8e6` | `b25d079fd23cbbfad1ae8cf4bb7eebe2ba57c0e2` | Shared `_evaluate_formula`/`_formula_in_note` with deliberately different labelled ASCII-colon semantics; also fork-specific derivative/correction integrations. **Partially overlapping, not equivalent.** |
| `grounding/release.py` | `ea90dce00c954b664597b24b1446e9e6372641b3` | `40774fe7ab5c7e7201b0ac9da50b31fcf00c0d43` | Substantial correction/release extensions; do not delete without targeted output comparison. |

Existing fork tests already exercise distinct behavioral contracts rather than just implementation: `test_grounding_derived_repair_coverage.py` (missing numerator/denominator, same-valued entity ambiguity, chosen scalars), `test_grounding_derived_preservation_gate.py` (complete repair/revalidation, mixed scope, snapshot/unit mismatch), and `test_grounding_note_formula_extraction.py` (reject text after ASCII-colon formula and preserve only complete labelled arithmetic). Do not duplicate these tests for a ceremonial audit.

**Decision:** no executable deletion currently proven behavior-equivalent. There is no safe code candidate or need for VM tests at this stage. Next engineering gate requires a *paired fixture run* in isolated detached worktrees (one upstream baseline and one fork), normalizing expected native issue codes while comparing: acceptance/rejection, authoritative call references, refusal to derive from unobserved financial operands, preservation after correction, and replay visibility. Scope to five to ten carefully selected cases; no full campaign. Only then create a single cleanup branch if an exact overlap is demonstrated. Keep all Asistente Casa-specific behavior, ISIN locks, and disabled-trading policy unchanged. No Risk ACCIONES or PR #71 work here.

## 2026-10-13 — derived multi-ref upstream candidate: VM PASS; publication blocked

- Candidate branch: `fix/upstream-derived-multi-refs`, head `0280842494e6761169bdffc1d66aaeda081cc3a3`, exact upstream base `300c6aa6989fcf2ec1cb052c89237b48fabdb524`.
- Diff: `agent/src/agent/grounding/policies.py` (+19/-1 in `_derivation`) and `agent/tests/test_grounding_multi_derived_refs.py` (70 new lines). No Asistente Casa features.
- VM detached worktree validation: `git diff --check` PASS, two `py_compile` PASS, multi-ref tests 6 PASS, native formula tests 46 PASS. Original production Vibe checkout unaffected; trading disabled.
- Contract: independently resolve semicolon-delimited derived refs; reject empty/unknown member; keep exact single-ref behavior and upstream's tolerant labelled-formula parsing.
- Attempted upstream PR creation via GitHub connector resulted in `403 Resource not accessible by integration`; **NO upstream PR exists as a result of this operation**. Do not claim submitted or merged. Candidate branch must be kept until successfully proposed or explicitly abandoned. This is not yet a reason to change fork main or deploy.
- Suggested upstream PR title: `fix(grounding): support multiple explicit references in derived figures`. Body should describe `before; after` incorrectly treated as one key in `_derivation`, independently scoped resolution with fail-closed missing entries, and focused validation above. Reference upstream issue #1622 as related background (not as a dependency).
- Once upstream accepts and merges, compare actual upstream implementation to the fork's more detailed operand-ref and correction contracts before retiring any local code.
