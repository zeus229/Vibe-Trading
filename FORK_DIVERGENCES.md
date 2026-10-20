# Fork divergences from HKUDS/Vibe-Trading

Last upstream sync: 2026-10-20 (upstream `fb1851ae`, merged onto fork `9160b7c7`).

## Sync 2026-10-20 (upstream `fb1851ae`)

Upstream `main` was force-pushed after 2026-10-10: the fork's previous sync commit `8e430079` is not an
ancestor of `fb1851ae`. Its rewritten equivalent is `23c26c8a` (same tree except one deleted session note),
so this merge used `23c26c8a` as the merge base. The default merge base (`026deaa9`, 2026-10-09) turns every
file added on both sides into an add/add conflict.

Decisions on top of upstream:

- **Entity isolation stays.** Upstream's keyed-metric recording (`instrument_scoped`, `instrument_symbols`,
  `_keyed_metric_map_kind`) is layered over the fork's `_EntityContext`/`identity_scope`. A symbol-less aggregate
  scalar grounds an unscoped claim only, never an instrument's, so
  `test_scalar_contribution_is_not_reclassified_as_an_instrument_map` asserts an empty per-instrument pool.
  `_field_sources` keeps keyed-map leaves (the MCP exact-ref contract needs them); `_metric_pool` drops them to
  avoid counting a leaf twice.
- **Derived-formula contract.** Upstream's `currency_code` filtering, `operand_transform` for point-valued
  inputs and structural `1` operand are adopted. The fork's `normalized_x100`, Asistente Casa `percent_points`,
  `strict_adapter_scope` and `_result_matches(note=...)` stay.
- **SEC filings.** `get_sec_filings` evidence takes the unit as ISO currency; a bare EDGAR ticker in a US
  payload is read as `<TICKER>.US` so it does not conflict with the market-qualified request symbol.
- **Loop/lifecycle.** `loop_grounding.py` and `tool_lifecycle.py` stay the fork's versions (upstream's are
  the same code without the repair contract, recovery stub, observed-tool-call ledger and `calculate_ppi_indicators`
  host-assigned call id).
- **Portfolio.** Native-currency refresh, `account_row`, `native_*` fields and the ARS editor stay.
  `PORTFOLIO_VALUATION_VERSION` is now 4 (upstream): snapshots stored at version 3 are hidden until the next
  `refresh`, so the first read after deploy needs a refresh.
- **br-data.** The standalone `vibe-trading-br-data` sidecar is gone (upstream); the daily sync is the system
  job `system:br-data-sync`, seeded only with `VIBE_TRADING_ENABLE_SCHEDULER=1` and `VIBE_BR_SYNC_ENABLED=1`.
  The deployment override sets the first flag but not the second, so no sync is seeded.
- **Server.** `proxy_headers` + `forwarded_allow_ips` in `server_cli.py` stay.
- **Docs.** README tool count stays 116 (measured by `tests/test_readme_counts.py`; upstream's 112 excludes
  the fork's tools).

## Branch cleanup after the 2026-10-20 sync

VM audit confirmed four remote branches removed from the fork after verifying no open PRs or associated worktrees:

- `fix/grounding-unitless-contribution-contract-20261016` (`3431cfd6`): ancestor of fork main; no exclusive commits.
- `contrib/upstream-keyed-analysis-metrics-squashed` (`7072e230`): matches merged upstream PR #1851; keyed-metric tests remain in fork main.
- `contrib/upstream-keyed-analysis-metrics` (`45a8a7fe`): superseded iteration of #1851.
- `contrib/upstream-blank-corner-matrix` (`7d6cb992`): superseded by merged upstream PR #1850; matrix tests remain in fork main.

The #1850 blank-corner matrix and #1851 keyed-analysis metrics fixes are now included via upstream and fork main; do not revive these contribution branches. The remote branches were removed, but local Git objects may still retain their tip commits. No worktrees were deleted: the seven remaining worktrees include production, PR #84, and local/unpublished investigations.

**Retained pending further review:** `fix/upstream-derived-multi-refs` (`02808424`) has a fork-only baseline fixture even though upstream PRs #1835 and #1837 were merged; `test/grounding-upstream-contract-probe` (`f8a40015`) has an unpublished comparison script. Neither is safe to delete based solely on upstream merge status. Other grounding branches, dirty worktrees and the old stash were not part of this cleanup.

## Follow-up branch and worktree audit — 2026-10-20

VM audit removed four additional remote branches, all ancestors of fork main with no open PR or associated worktree:

- `fix/grounding-partial-contract-retry-20261015` (`9864c711`), merged PR #79.
- `fix/grounding-exact-ref-line-scope-20261015` (`79545232`), merged PR #80.
- `fix/grounding-integral-report-20261015` (`51801308`), merged PR #81.
- `fix/grounding-spaced-point-units-20261015` (`a716b552`), merged PR #82.

**Local closure (operator-confirmed, 2026-10-20):** `fix/grounding-partial-contract-retry-20261015` (`9864c711`) was deleted locally after `git merge-base --is-ancestor <branch> main` passed and `git cherry -v main <branch>` produced no exclusive patches. `git branch -d` refused because the checkout was not based on the fork's `main`; the operator used `git branch -D` only after the explicit ancestry check. The rejected `portfolio_summary` optional-`snapshot_id` hint experiment was dropped as `stash@{0}` after verifying its exact object SHA `3fb5f902252ce7f46e82e3be03d62fa31c2b56fd`; command output confirmed `PASS`. No other local branch or stash was removed by these commands.

**Remote configuration caveat:** on the VM checkout `~/vibe-trading`, `origin/main` resolved to upstream `fb1851ae` while local `main` resolved to fork merge `91990088`. Consequently `git cherry -v origin/main <fork-branch>` gave misleading hundreds of entries. Verify remote URL and tracking configuration before future cleanups; compare against the confirmed fork `main`, not blindly against `origin/main`.

No worktrees were removed. Production and PR #84 remain protected; development and grounding worktrees contain uncommitted changes. Retain fork-only branches pending content review, including `fix/canonical-equity-risk-grounded-output`, `fix/grounding-ratio-display-failclosed-20261014`, `feat/hermes-package-comparison-session`, `fix/upstream-derived-multi-refs`, and `test/grounding-upstream-contract-probe`. This is a reference-cleanup record, not evidence that remaining customizations are redundant.

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

## Consolidated fork minimization audit — 2026-10-13

**Scope/baselines:** fork production `1d906e68d1c2fa89634df5d68e0de6845ee9ea2c`, integrated upstream `300c6aa6989fcf2ec1cb052c89237b48fabdb524`. Read-only comparisons via GitHub fetched source and blob hashes, plus two previous VM detached-worktree differential campaigns (9/14 cases) and upstream-fix VM PASS (6 new + 46 existing). No new runtime executions were performed during this consolidated audit: GitHub cannot execute Python inside the VM and the analyst's container has no GitHub network/DNS. Do not mistake source-level findings for an E2E PASS.

### Decision matrix

| Area | Native upstream already provides | Fork-specific delta | Classification | Generic upstream value / required gate |
| --- | --- | --- | --- | --- |
| Formula checks | Native price evidence, result mismatch, missing and additive operands, native labelled formula tolerance. 4/5 native fixture outcomes identical in previous VM differential. | Multi-source ref parsing, evidence identity/units/snapshots, specific entity/unit conflicts, complete correction refs. | **Keep, simplify after equivalence** | High: multi-ref fix already tested in branch `fix/upstream-derived-multi-refs@02808424`; do not open PRs yet. Identity and correction need separate semantic tests. |
| Correction | `release.py` correction prompt, `_passed_figures` from `ledger.py`, rejected-figure release/redaction. | `repair_contract.py` absent upstream; `CorrectionContract`, `derived_repair_verified`, per-issue preserve/replace/derive, >24 issue queue. | **Keep** | High, but prove whether correct evidence-backed figures are omitted following real correction; paired fixture with revalidation and retained refs, before deciding PR shape. |
| Compact/replay | `_replay_context_result`, `_microcompact(preserve_tool_call_ids)`, `_context_collapse` with replay-call preservation. | `_microcompact(preserve_tools)`, tool-class `preserve_during_microcompact`, extra tool lifecycle safeguards and replay lease. | **Keep / investigate partial duplication** | High/medium: show compact result survives *both* layers and first successful model request with an exact call-id trace. Upstream **does** have replay preservation; do not claim the entire feature is missing. |
| Portfolio snapshots | Native `snapshot_id` pinned read, `read_identity`, `snapshot_not_found` as previously integrated #1769. | Compact view, `no_cache`, ARS/native currency and unsettled-cash representation, custom invalid-snapshot response. | **Retain canonical view; investigate freshness** | Medium: compare omitted/pinned IDs, absent snapshots, cache bypass and identical snapshot result; no deletion before consumer audit. |
| Technical indicator identity | Native `end_date`, bars fingerprint, `read_identity.provenance` (#1768). | `no_cache`, provenance-inclusive fingerprint, requested-start/latest extra fields, preserve flag. | **Investigate/simplify if consumers allow** | Medium: compare equal bars/different provenance, changed bars, missing volume, index dates and cache/replay usage; fingerprint hashes are NOT interchangeable. |
| ISIN/listing | `extraetf` parses/checksums ISIN, `portfolio/service.py` groups by ISIN and excludes unresolved venue risk series, Trading212 and Brazil capture ISIN. | Verified mapping `ISIN + source + venue + qualified symbol` from Asistente Casa locks a .BA quote identity (provider check) in `grounding/identity.py`. | **Retain local integration; propose generic resolution contract** | High: provider-neutral verified-listing contract using existing upstream identity machinery; fail closed on same ISIN multiple venues, wrong source, unverified and cross-provider mismatch. Do NOT claim upstream lacks ISIN support. |
| Codex optional arguments | `strict:false` already identical blob, #1785. | None at checked SHAs. | **Absorbed (no code removal)** | None; keep native behavior. |
| Proxy, Argentina policy and trading gate | No equivalent requirements established. | Cloudflare forwarding, trading disabled, local canonical tools, deliberate ASCII colon and 0.5% rounding. | **Conserve** | Do not propose as generic bug without separate evidence. |

### Evidence and execution boundaries

1. Previous VM probe 14 cases: five **native quote** controls yielded 4 equal outcomes; the fifth reflected deliberate ASCII-colon policy. Nine generic records exposed different reference parsing / identity scope and **cannot alone isolate numerical validity**. The native multi-ref upstream deficiency was isolated via explicit single vs multiple refs.
2. Standalone upstream-based change `fix/upstream-derived-multi-refs@0280842494e6761169bdffc1d66aaeda081cc3a3`: 6 dedicated regression tests PASS; 46 native formula tests PASS; syntax and diff checks PASS in VM. Remains unmerged and not deployed; **no upstream PR to be created yet**, per current user instruction.
3. Source comparison inspected upstream/fork `grounding/{policies,release,repair_contract,identity}.py`, `context_compact.py`, `portfolio/service.py`, `technical_indicator_tool.py` and ISIN readers. Absence or blob difference is evidence of an implementation delta, **not** by itself proof of different runtime semantics.
4. No new VM differential runs for correction, compaction, snapshot fingerprints or ISIN in this consolidated audit. These remain **unverified** and must not be labelled test PASS.

### Remaining minimum targeted experiments before deletion or upstream submission

- **Correction (3 cases):** (a) a corrected exact ref must preserve a verified figure; (b) an ambiguous same-valued pair cannot automatically preserve the wrong entity; (c) a draft with >24 issues must not silently drop rejected or verified claims. Record final output and call IDs, not merely issue counts.
- **Compaction/replay (3 cases):** replayed call before model read; read after successful model request; protected compact tool evidence across both microcompact and context collapse. Compare both implementation outputs in detached worktrees.
- **Snapshot/fingerprint (3 cases):** equal bars and changed provenance; pinned immutable snapshot after latest changes; no_cache explicit fresh observation. Audit consumers before fingerprint change.
- **ISIN (3 cases):** one verified listing, two venues for one ISIN, conflicting/unverified provider mapping. Preserve fail-closed semantics and distinguish a security from a listing.

**Final gate:** No evidence currently supports a production code deletion. Group generic contributions by stable contract rather than file or historic issue; postpone all upstream PR creation. No Risk ACCIONES or PR #71 work; no production/secret/trading changes. Avoid a second documentation wiki: this section is the official fork divergence record.

## 2026-10-13 — 12-scenario VM differential results (Claude report)

**Evidence source:** user-provided VM test report; two detached worktrees (fork `1d906e68`, upstream `300c6aa6`), targeted test suites **352 PASS fork / 130 PASS upstream**, synthetic offline probes for correction, replay, fingerprints, and ISIN. Evidence was reported under a local `/tmp/claude-1000/.../scratchpad/evidence/` path on the VM, **not imported to GitHub**. These results are operator-reported, not independently rerun in this GitHub session. No full agent/model or production E2E.

### Updated decisions based on executed probes

- **Correction — KEEP, generic upstream candidate.** On exact bad call ref, both gates suggest the correct candidate. Upstream accepts a revised answer that simply omits a reparable 1.79% claim; fork's `CorrectionContract.missing_figures` rejects its disappearance in correction lifecycle. On equal-valued rows, upstream can accept an ALPHA claim backed by BETA's row in generic `entity_id` schema; fork rejects `entity_ref_needs_scoped_claim`. With 30 rejected figures upstream prompt names 24 and warns 6 are omitted; fork compact queue gives all 30, at ~5k extra chars. No false claim that upstream conceals remaining issues: it warns explicitly.
- **Replay lease — native / SIMPLIFY.** Both implementations pass lease call IDs from four production `loop.py` sites, preserve until model request and release after visibility. Fork's wrapper default lease propagation covers no presently identified call site; fork clears pending lease upon a write, whereas upstream retains it. Isolate these minimal deltas before deleting, not lease mechanism wholesale.
- **`preserve_during_microcompact` — KEEP, generic candidate.** Fork preserves flagged tool output in Layer 1 only; Layer 2 collapse can still truncate a >2400-char flagged result. Upstream has call-ID replay protection but no tool-name opt-in. Do not describe fork flag as preserving across both layers.
- **Fingerprints — potential REMOVE after persisted consumer audit.** Upstream SHA distinguishes bars, fork SHA distinguishes bars and provider provenance, and the JSON canonicalization differs even without provenance. Source search did not find local application-code consumers of custom SHA or extra start/latest metadata, but persistent artifacts were not audited. Upstream returns provenance separately.
- **Pinned snapshot ID — native / SIMPLIFY.** Both sides return stable bytes for pinned reads after latest refresh; nonexistent/blank ID fails closed. Keep local `view=compact` and ARS-native semantics. Inspect exact fork-only snapshot code before deletion: this is a behavioral decision, not evidence that a particular line is removable.
- **`no_cache` — KEEP schema exposure and portfolio replay.** Generic upstream replay bypass already honors `no_cache` at lifecycle level; fork advertises it in tool schemas. Fork `portfolio_summary` replays latest and pinned after compaction, while upstream re-executes; this can change an analytical snapshot mid-session. Generic upstream candidate.
- **Verified portfolio ISIN — KEEP; urgent ambiguity investigation.** Native extraETF parses/validates ISIN and leaves venue unresolved. Fork's `_ingest_trusted_portfolio_identities` locks verified Yahoo/ISIN/BYMA `.BA` market symbols. **New defect reproduced synthetically:** key `portfolio:{isin}` is overwritten when two verified `.BA` listings share ISIN (last row wins); same market-qualified symbol with two ISINs may produce two independent locks. Confirmed source in `agent/src/agent/grounding/identity.py`: row loop writes `self._identities[key]` without a distinct-listing / conflict check. No evidence of actual production collisions yet; must NOT claim deployed users affected. Require fail-closed ambiguity handling and explicit tests *before* designing a provider-neutral upstream PR. Same ISIN across venues is not necessarily an error; represent distinct market listings explicitly instead of inventing one canonical ticker.
- **Multi-ref fix — validated candidate `02808424`, KEEP pending future upstream proposal.** User explicitly requested no PRs yet.

### Next gates, in priority order

1. **Safety/correctness fix in fork (normal branch, VM validation before main):** synthetic tests for duplicate ISIN with different `.BA` symbols and same symbol with distinct ISINs, plus single unique verified listing and invalid mappings. Inspect current Asistente Casa `holdings_native` read-only shape (no private data committed). Design fail-closed resolution without silently replacing earlier lock. Only deploy after local VM PASS.
2. **Small cleanup:** identify exact redundant fork call paths in replay wrapper, snapshot ID, and indicator fingerprint. Require surgical diff and narrow tests; avoid deleting broad modules.
3. **Future upstream grouping (NO PR now):** (A) multi-ref; (B) replay opt-in + portfolio replay schema; (C) evidence identity/row scoping; (D) correction retention/issue queue, later; (E) provider-neutral ISIN listing after local collision fix. Keep each contract independent unless reviewers request grouping.

**Scope exclusion:** Risk ACCIONES and fork PR #71. Asistente Casa remains canonical, trading remains disabled. No E2E/production conclusions.

## 2026-10-13 — upstream issue #1820: percent-unit double scaling

- New upstream report: https://github.com/HKUDS/Vibe-Trading/issues/1820 — a declared `-1.89%` with evidence/formula already in **percentage-point units** (`-1.89`) may be rejected and a 100x number accepted. Reporter cites production correction loops; those logs were not independently reviewed here.
- Confirmed at upstream `300c6aa6`: `grounding/policies.py::_result_matches(figure, result)` uses `{result * 100}` unconditionally for `figure.percent`.
- **Fork has a relevant but narrower existing exception:** `_result_matches(figure, result, note="")` chooses `{result}` only when `_has_explicit_percent_scale(note)` and **not** `_has_trailing_percent_factor(note)`; otherwise it still multiplies by 100. This cannot be claimed to solve issue #1820 globally. In particular, the explicit trailing `*100` path and observed percent-unit fields require independent probes.
- Do NOT blindly accept both `result` and `result*100`: doing so without trustworthy field units can authorize 100x-wrong figures. Evaluate evidence `unit`/field metadata and exact note arithmetic; fail closed if unresolved.
- Add focused regression matrix before any generic percent-policy PR: observed percent-unit `15.1` claimed `15.1%`; same input falsely claimed `1510%`; fractional `0.151` claimed `15.1%`; derived result `-1.89` with explicit trailing `*100`; already-percent-point result `-1.89` without trailing scale; Spanish decimal comma and negative sign. Run against upstream and fork with identical synthetic evidence, checking valid/reason and avoiding entire-agent E2E.
- Keep this work **separate** from ISIN candidate `fix/isin-verified-listing-conflicts` (different files/contracts), but include it in the consolidated grounding/PR planning. Current user instruction: no upstream PR creation yet. No runtime, production, or trading changes.

## 2026-10-13 — Candidate removal of absorbed upstream extensions

**Basis:** Claude offline comparative audit of fork `837c4ca4` vs upstream `300c6aa6`, with X1, X2, X3 and consolidated XALL synthetic ablations. Proposed branch: `chore/reduce-absorbed-upstream-overrides` (never deployed).

- **X1:** remove fork-only default replay visibility lease injection in `AgentLoop._microcompact_and_unblock` and extra visibility-lease clearing on a write. Production callers explicitly pass replay lease IDs; upstream lifecycle already consumes them. The known two fork-specific tests of these deliberate extras must be identified and rewritten or retired upon validation, not silently ignored.
- **X2:** restore upstream `_dataset_fingerprint(close, volume, interval)` with bars-only hash, keeping provider provenance separately in `read_identity.provenance`. Offline test: 49 PASS; persisted code and session audit found no strict fingerprint consumers.
- **X3:** restore upstream `PortfolioService.snapshot_by_id`; current contract retains immutable pinned identity and checks connector activation/valuation version. Offline test: 338 PASS.
- **Consolidated offline XALL:** 2520/2522 focused tests passed; only the two fork tests asserting retired replay extras failed. This result belongs to Claude's temporary worktree, not to this new GitHub candidate, which still requires fresh VM validation.
- **Preserve:** `preserve_during_microcompact` Layer-1 opt-in, `portfolio_summary.replay_after_compaction`, `no_cache` schema, ARS compact view and native holdings, portfolio ISIN conflict protection, grounding identity/operands/`CorrectionContract`.
- **Defer:** `_missing_snapshot`, `requested_start_date`/`latest_date`, generic upstream PRs, issue #1820.
- **Gate:** test replay lease wrapper + all direct real loop callers, indicator SHA invariants and provenance, stable pinned snapshot and revoked source cases, 2522 focused tests minus/update two obsolete assertions, diff check and compilation. Report any regression instead of changing versioned code on VM.

Do not merge or deploy until the candidate passes VM validation. Keep `PPI_TRADING_DISABLED=True`.

### Candidate validation update — 2026-10-13

Claude validated candidate `ed394f7e` against XALL: exact same source diff (79 changed source lines, zero discrepancies), `git diff --check` and compilation PASS, 2,520 of 2,522 targeted tests PASS. Only two outdated replay-lease tests failed; all 11 differential probes matched expected changes. The two tests in `test_readonly_replay_after_microcompact.py` have now been adapted in GitHub to explicitly pass pending lease IDs and assert that writes clear replay cache/readiness while leaving lease consumption to the next model request. **These GitHub test changes remain unvalidated on the VM.** Repeat targeted suite before integration; no production deploy or upstream PR.

## Consolidated grounding/upstream triage — 2026-10-13 (GitHub recheck)

Scope: repository-only audit, no runtime changes. Baseline supplied by operator: production fork `e5564e3730c16200136f7d0d5491dfd7dc6dde46`; production status is not inferred from GitHub. The open fork PR #71 is excluded from this general grounding cleanup and remains an independent risk-report deliverable.

| Finding / owner / reference | Verified GitHub state | Status / priority | Risk to AC / dependency / next validation |
|---|---|---|---|
| Declared grounding registry; upstream #1622, #1691 | #1622 closed as completed 2026-10-13; #1691 merged; maintainer reports all 15 check families and passing/failing fixtures | RESOLVED / no new patch | Policy transitions still require differential behavior tests before dropping fork checks |
| Derived operand completeness; upstream #1622, maintainer reply | Explicit missing operands rejected; scalar parameters require visible labelled declaration | RESOLVED upstream / P2 adoption gate | Compare fork-only derived.py against native using supported, unsupported, missing and scoped operands; no blind deletion |
| Outer percentage `*100`; #1622 | Maintainer confirmed narrow normalization only for outer trailing literal on percent declarations | RESOLVED upstream / P2 | Check sign, denominator/numerator evidence and inner constants with symmetric paired fixtures |
| Already-percent units; upstream #1820 | Issue open; distinct from the outer-literal patch | PENDING / P1 | 100x false positive or valid percentage dropped; probe fraction vs percentage-point values and locale/sign with trusted unit metadata; avoid accepting both scales unconditionally |
| Multiple evidence references; fork branch `fix/upstream-derived-multi-refs@02808424` | Branch exists; earlier operator-reported 6 regression tests plus 46 native formula tests PASS, not retested against latest upstream | NEEDS VALIDATION / P1 | Rebase/port only after differential fixtures on current upstream; independent future generic PR, none opened |
| CorrectionContract / verified-figure retention; upstream #1702 | #1702 merged (clean figure keep-list up to 24); fork additionally rejects dropped required figures during correction | NEEDS VALIDATION / P1 | Mixed 2 invalid + 6 good claims, >24 issues, corrected exact ref, no numeric disappearance on final answer |
| Cross-entity attribution; fork identity and scoped-claim contract | Prior detached VM probe found same-valued ALPHA/BETA scope difference; no new check on current upstream | NEEDS VALIDATION / P1 | False attribution risk for portfolio/benchmark; two equal amounts with distinct entity ids and conflicting refs; reject cross-scope |
| Canonical risk report; fork PR #71 | Only open fork PR found, draft, not merged; E2E gate explicitly pending | BLOCKED / P1 separate lane | Exact tool_call_id, full matrix/13 contributions, correction trace; no deployment before authenticated E2E |
| ISIN conflict correction; fork PR #73 | PR exists; operator reports merged and deployed; repository historical PR description includes offline 507-pass validation | RESOLVED fork / monitoring | Fail-closed ambiguous listing; re-test if relevant identity upstream semantics change |
| Fork override pruning; PR #74 | PR exists; operator reports merged and deployed; prior candidate 2522 test PASS | RESOLVED fork / P3 | Do not recreate redundant replay wrapper, upstream fingerprint or snapshot_by_id implementation |
| Codex optional arguments; upstream #1785 | Contributor PR #1785 closed **unmerged**, but prior 2026-10-13 blob comparison shows native upstream `strict:false` same as fork | RESOLVED behavior / doc clarification | Never claim #1785 was merged. Retest schema only if provider changes; no independent patch needed |
| Replay lease, microcompact, `no_cache`, compact ARS snapshot | Upstream lease lifecycle native; microcompact tool opt-in/portfolio replay/compact ARS remain fork-specific per previous probes | NEEDS VALIDATION / P2 | Two-layer compaction and pinned reads, before attempting additional deletion |
| Upstream contributions #1767/#1768/#1769, #1702/#1728 | Already merged; #1728 tolerant labels differ intentionally from fork fail-closed ASCII colon policy | RESOLVED; deliberate divergence / P3 | Do not resubmit; preserve tests and explicit strict-label policy |
| Research Goal provenance; upstream #1688 | Previous upstream contribution; direct current functional regression not executed | NEEDS VALIDATION / P2 | Synthetic add_goal_evidence exact call ids; do not treat older occurrence as open incident |
| Upstream changes after fork base | Upstream latest observed via commit search `54f43595` (following `300c6aa6`); multiple recent backtest, memory, providers and portfolio PRs | NEEDS VALIDATION / P2 | Compare only touched interfaces affecting current integrations; no broad sync solely for new features |

Execution order: (1) P1 paired grounding corpus using current upstream/fork and current production commit as comparison anchor; include #1820, multi-ref, correction retention and entity attribution. (2) Independently finish #71 E2E gate if prioritized as a report bug; do not mix with general upstream cleanup. (3) Identify only behavior-equivalent remaining fork overrides, one narrow candidate with VM validation. (4) Once tests prove new generic gaps, draft independent upstream proposals; do not open upstream PRs before review. (5) Optional enhancements later.

Closure criteria for each stage: pinned SHAs, reproducible synthetic fixture and expected deny/allow reason, focused tests in detached worktrees, reviewed diff if any, and production validation only after a separately authorized deployment. `PPI_TRADING_DISABLED=True`; Asistente Casa owns positions, valuation, history and metrics; Vibe remains read-only. Do not mix with AC investment-service incidents. No functional changes authorized by this audit.

### Stage A — source differential checkpoint (2026-10-13)

This is a **GitHub source inspection, not an executed offline VM differential test**. Current heads pinned for comparison: fork `e97b2aa31cb8111c4fc60a48789218e5788078a4`; upstream `54f43595597e823facc3daae095aa9d671e058e8`. Both repos expose the same blob `79a01a2c8b7504551dab511dd18207aad85f977f` for `agent/tests/test_grounding_operand_evidence.py`; this establishes test parity for that file only, not behavioral parity.

1. **Operand evidence:** both current `grounding/policies.py::_derivation` versions reject unknown non-scalar operands and exempt only the literal identity 1 or declared scalar parameters. Both normalize an *outer* trailing literal `*100` before the evidence gate for percent figures. **Status: RESOLVED upstream contract; no new patch.**
2. **Multiple exact refs:** upstream `_derivation` now explicitly calls `_referenced(declaration.ref,...)` and adds referenced values as anchors, with a comment on cross-run arithmetic. The fork additionally splits exact refs, enforces one unambiguous `call_id::field` record per key for generic financial evidence, then invokes `validate_operands`. Branch `fix/upstream-derived-multi-refs` currently differs from fork main by 3 commits ahead / 647 behind; files `policies.py` and `test_grounding_multi_derived_refs.py`. **Status: NEEDS VALIDATION, not an approved cherry-pick or upstream PR.** Run its six intended fixture cases on upstream head without introducing a branch, including refs to two different calls and missing/ambiguous refs.
3. **Percent units:** upstream `_result_matches` still uses `{result*100.0}` for percent claims without unit-based selection. Fork's note-aware version offers an extra explicit-scale route, but it is not a full unit contract. **Status: PENDING #1820; high-risk 100x ambiguity.** Pair true percent-point, fractional, wrong 100x, explicit outer `*100`, locale comma and negative examples.
4. **Correction:** upstream #1702 keeps verified figures in bounded prompt instructions; fork `repair_contract.py::CorrectionContract.missing_figures` makes *rejected claims with deterministic preserve directives* mandatory during correction. This is narrower than requiring all clean values to remain. **Status: NEEDS VALIDATION, preserve fork mechanism.** Probe 2 rejected/6 clean, 30 rejected, omission of required repaired claim; compare final release, not only correction prompt.
5. **Entity identity:** fork has explicit `entity_ref_needs_scoped_claim` in policies/release; upstream code differs. **Status: NEEDS VALIDATION.** Probe identical numerical values attached to ALPHA and BETA with cross-references and verify exact denial reason.
6. **Risk PR #71:** independent draft, keep separate from stage A; existing live E2E validation gate unchanged.

**Stage A completion gate:** execute equal synthetic fixtures against both pinned SHAs in detached checkouts, capture verdict/issue reason, actual referenced call IDs and correction outcomes; inspect upstream test coverage before declaring the multiple-ref candidate obsolete. No broad suites required. No secrets, write APIs, runtime deployment, or trading. Do not describe this source checkpoint as runtime PASS.

### Stage A — executed differential validation, operator report (2026-10-13)

**Supersedes the preceding source-only checkpoint where it inferred strict fail-closed multiple-reference behavior from code.** This section records an offline VM report supplied by the operator; evidence scripts/JSON were retained in a VM-local `/tmp/claude-1000/.../scratchpad/diff/` path, not committed. GitHub source was previously inspected, but these executed results have not been independently rerun by ChatGPT. Fork `7a06d9710365fed6763aded784086d1ca7f1f8d4`; upstream `54f43595597e823facc3daae095aa9d671e058e8`; candidate `02808424` based on upstream `300c6aa6`. Identical synthetic fixtures, detached worktrees cleaned; no production changes or private data; `PPI_TRADING_DISABLED=True`.

| Invariant / dependency | Upstream observed | Fork observed | Decision and closure gate |
|---|---|---|---|
| Observed/missing operands, declared scalar constants | PASS | PASS | RESOLVED for tested cases; **limitation**: either side allows missing value declared visibly as `count` parameter, so parameter label semantics remain unproved |
| Fractions, final outer `*100`, negatives, Spanish `12,4%`, downward 100x | PASS | PASS | RESOLVED in tested shapes; do not weaken this |
| Percentage-point derived `0.82% | derived | 0.52 + 0.30` | FAIL `derivation_result_mismatch` | same FAIL | PENDING #1820 / P1: unit-aware derived interpretation |
| False upward scale `0.52 → 52%`, `15.1 → 1510%` | FAIL: accepts wrong 100x | same FAIL | PENDING #1820 / P1: fail closed when unit metadata says percentage points; highest numeric integrity risk |
| Percent-point derived with two exact refs from same call | FAIL `no_evidence` | PASS | KEEP fork / P1; test source resolution across same/different call IDs |
| Multiple refs, positive and one valid ref | 2/6 PASS | 2/6 PASS | Current upstream/fork do **not** reject unknown or empty refs reliably: `before;unknown`, `unknown;after`, `before;`, `;after` all yielded `valid=True`; also `ghost::close` and nonexistent fields passed. **Corrects earlier assertion that fork strictly rejects every bad exact ref.** |
| Candidate multi-ref `02808424` (six target cases) | Not ported to current head | Not integrated | **6/6 PASS on candidate's old base** and rejects unknown field / ghost ref; KEEP candidate, NEEDS VALIDATION after non-destructive port to upstream head / P1 |
| Correction rejects loss of required reparable figure | FAIL, silently releases | PASS, `CorrectionContract.missing_figures` reports `20.36` | KEEP fork; for >24 issues fork identifies all six omitted from bounded prompt; upstream releases. Clean non-required figures are not hard-mandatory by design |
| Cross-entity ALPHA/BETA claim using wrong row | FAIL, accepts | PASS `entity_ref_needs_scoped_claim` | KEEP fork entity isolation; investigate intentional `aggregate_ref_needs_unscoped_claim` distinction separately, not this bug |

**Suites reported:** fork 1,904 passed; upstream 1,110 passed. Additional fork tests run against upstream: 66 passed, 73 failed (not all failure reasons triaged; many tied to missing entity isolation). `ast.parse` passed for 33/31 agent Python files; `py_compile` **not run**. `git diff --check` clean. No full correction loop, E2E, real tool evidence, complete suite, frontend, Docker or production validation. Same-call refs behavior and nonmarket redact-mode discrepancy not yet explained. The existing working checkout had 44 pre-existing uncommitted entries; untouched.

**Classification / next actions:**
1. **BLOCK simplification** of identity, correction and derived-ref fork behavior. Upstream is not behavior-equivalent.
2. **P1a numeric safety:** design a strict percentage-unit contract for #1820 with positive/negative, fractional, already-percent, locale and 100x adversarial fixtures, using trustworthy evidence field-unit provenance. Never accept `result` and `result*100` indiscriminately.
3. **P1b evidence integrity:** preserve and non-destructively re-port the small `02808424` multi-ref fix against current upstream; rerun six original fixtures + ghost/nonexistent fields + same-call two refs, then decide whether a *generic* upstream PR is justified. No upstream PR yet.
4. **P1c preservation:** keep `CorrectionContract` and entity isolation; only revisit after upstream gains equivalent tested guarantees.
5. **Separate follow-ups, not Stage A blockers:** assess `aggregate_ref_needs_unscoped_claim` false rejects and loop/E2E correction behavior. Fork PR #71 stays an independent workstream.

**Stage A verdict:** offline differential stage COMPLETE for specified synthetic scope with documented FAILs and limitations; no permission implied to merge, deploy, or modify functional code. Recommended first functional implementation: #1820 100x false-positive prevention in a single fork candidate with focused VM validation and an upstream-generic reproducer, then the separate multi-ref candidate. Actual production exposure remains unverified.

### 2026-10-13 — avoid duplicate #1820 fix: upstream #1823 review gate

GitHub upstream [PR #1823](https://github.com/HKUDS/Vibe-Trading/pull/1823) is **open/unmerged** at `e5f5f3d55942713a52ec525ae5db1f7443089a84`, based on `300c6aa6`. Issue #1820 has maintainer confirmation of its fix, but issue closure and code inclusion in later upstream main were **not** proven here. PR author reports 1126 grounding tests PASS, six focused new cases, with no manual/live testing.

Patch review: detect evidence fields containing `percent`/`pct` or ending `%`; for a *derived* percent declaration, interpret formula output in points only if each data operand anchors solely to percent-named observed candidates and no outer explicit `*100` was normalized. Reject 100x figure, preserve fraction interpretation on mixed/colliding fields. Does **not** cover `proposed` declarations, or imply a universal unit schema.

**Decision:** do not independently implement #1820. Before adopting, test upstream PR head in detached worktree against Stage A cases, then test an isolated integration candidate against fork HEAD with its distinct `_derivation` tuple and `_result_matches(note=...)` signatures. Include `changepercent 0.52/1.41 → -0.89%` valid and `-89%` invalid, `0.52 + 0.30 → 0.82%` valid, positive 100x invalid, locale and negative, explicit `*100`, mixed unit/collisions, two exact refs in one call, and no regression in fork identity/repair. Unit field-name inference must be reviewed against canonical structured `*_pct` fields and ambiguity. Do not merge/deploy until VM PASS.

**Independent multi-ref work:** old `fix/upstream-derived-multi-refs@02808424` is a validated six-case patch against `300c6aa6` only. Compare shows just `grounding/policies.py` (+19/-1) and new `test_grounding_multi_derived_refs.py` (70 lines). Stage A found unknown/empty refs were silently accepted on both current heads; do not abandon this fix. Prefer non-destructive update of the existing branch if safe, preserving its unique history; no new branch solely for validation, no upstream PR until the rebased candidate passes focused tests. Keep #1823 and multi-ref implementations as **separate** contracts.

### 2026-10-13 — #1823 port integrated into fork main; deployment pending

GitHub integration **completed by fast-forward** from `ce5f47f4783554fe03657906a1ca99390841a9b4` to `e8c9d736c4173161ee458085d3a515d4c4e1e041` on `zeus229/Vibe-Trading/main`. Source `agent/src/agent/grounding/policies.py` adapts upstream PR #1823 (still open when checked) to local `_derivation` triple return, financial exact-ref early return, and `_result_matches(note=...)`; upstream's six tests are added unchanged in `agent/tests/test_grounding_percent_unit_fields.py`. This is a **fork-only backport**, not an upstream merge or a confirmed production deployment.

**Validation evidence:** operator-reported detached VM test run on exact `e8c9d736` (no rerun needed): six new tests PASS, 1,906 focused grounding tests PASS, 405 fork-specific regression tests PASS, 14/14 Stage A unit cases PASS, prior 86 comparison cases unchanged, `ast.parse`, `py_compile`, `git diff --check` PASS. No full agent loop, E2E, Docker or live production verification. GitHub compared the candidate to main and verified two files only, ahead 2/behind 0, then fast-forwarded using an expected-SHA guard.

**Scope boundary still OPEN / P1:** the port corrects percent-point derivations for market evidence but does **not** safely infer percent units on generic `asistente_casa_*` MCP `*_pct` fields nor other generic tools. Synthetic `top5_pct - top1_pct` can still reject 41.2% and accept 4120%; `change_percent` in a generic tool has the same 100x gap. Do not claim #1820 globally solved for Asistente Casa. Before widening the matcher, design an explicit trustworthy unit/identity scope for structured generic evidence and add adverse 100x, mixed, absent and wrong-ref fixtures. Keep that work independent of generic multi-ref `02808424` and separate from PR #71.

**Deployment gate:** fork main code is integrated but not deployed. Only deploy via a separate authorized VM operation, verifying deployed image SHA and read-only/trading-disabled mode. Once upstream #1823 merges, differential-test upstream native versus this port and remove duplicated fork code only if the same contracts pass.

### Candidate — exact canonical percent-point refs (not validated or deployed)

Branch `fix/scoped-percent-unit-evidence` extends the upstream #1823 backport **only for verified exact financial refs** from `asistente_casa_*` records of `unit=ratio` with percent-named leaf fields, and only when no trailing explicit `*100` was normalized. The early financial `validate_operands` gate still validates temporal identity, units and full operand coverage before permitting a derived claim. Fraction-valued/non-percent refs retain prior scaling; the market-data #1823 path is untouched. Source delta: `agent/src/agent/grounding/policies.py`; synthetic cases: `agent/tests/test_grounding_canonical_pct_units.py`.

**Not established:** whether all current Asistente Casa producers expose `top1_pct`/`top5_pct` as `unit=ratio`, and whether their exact refs enter the financial fast path under these fixtures. Validation must inspect observed evidence records, run tests, and return FAIL rather than inferring semantic correctness from names alone. Unknown or ambiguous refs must remain fail-closed. Generic tools outside the canonical adapter are explicitly not broadened by this candidate. No production or upstream PR until targeted VM PASS, reviewed diff and integration decision.

### 2026-10-13 — canonical percent-point exact-ref candidate integrated

Fast-forward on fork `main`: `9a15abb61b0de3d6a850b72719f896e26003ea59` → `0bee9eb91d9b69118cdcc5969fa58dbb0928f523`, after verifying three changed files and no divergence. Operator ran detached VM tests against exact candidate: 1,910 grounding tests PASS, 416 fork regressions PASS, 10 new+upstream unit tests PASS, 14/14 market units PASS, compilation/AST/diff PASS; 86 existing Stage A cases unchanged. Real deployed MCP/E2E/loop and production still **not verified**. Source review supports canonical fields such as `top1_pct`, `top5_pct`, `weight_pct` already denoting points; synthetic records verified `unit=ratio`, `identity_scope=aggregate`, `status=observed` and exact ref resolution.

**Important residual risks:** (1) call-level `ref=risk` remains unsafe for this unit distinction: can reject correct `41.2%` and accept `4120%`; favor explicit `call_id::field` in derived claims and diagnose actual model output patterns before altering prompt/gate. (2) Mixed exact percent/plain refs retain fraction fallback, potentially accepting a 100x figure if the plain field was secretly points. (3) Generic tool names and nonexact refs remain out of scope. (4) Field-name inference on unexpected paths (e.g. `pct_change_of_fraction`) has not had adversarial E2E testing. None of these gaps is closed by the integrated patch. Any follow-on strengthening needs its own synthetic negative tests, deliberate compatibility review and VM validation. No trading or deploy.

### 2026-10-13 — production deployment report: 8a305e91

**Operator-reported production PASS** on Vibe fork `8a305e914746a9ef78a342114730ae242d356fc8`, upgraded from `e5564e3730c16200136f7d0d5491dfd7dc6dde46`. Image `vibe-trading:prod-8a305e91` (`sha256:a1225cda47af…bea498`), one healthy container, zero restarts, `/live` and `/health` HTTP 200, `PPI_TRADING_DISABLED=True`, unchanged hashes for private configuration and scheduled jobs. Synthetic grounding smoke PASS 11/11 both before and after rollout; no real agent conversations, correction-loop E2E, or scheduled research run. Previous image `prod-e5564e37` retained as rollback; no rollback required. Other Asistente Casa services not restarted. Reported checkout `/home/zeus229/vibe-trading-prod` fast-forwarded to deployment SHA. No real-value exposure or trade tests were performed.

**OPEN operational risk — must address before another Compose operation:** root-owned `/etc/asistente-casa-vibe.override.yml` still names `prod-e5564e37`, despite deployed image `prod-8a305e91`. A routine Compose restart using the persistent override could unintentionally roll back. Agent should safely align only that private image reference to the proven new image after backup, compare effective Compose configuration, preserve secrets/volumes and keep the previous override/image for rollback; verify running image stays unchanged. This is a VM-private operational task, not GitHub-versionable. The build worktree `/home/zeus229/vibe-build-8a305e91-20261013` remains and can be cleaned up only after ensuring no runtime dependency, using non-destructive worktree removal.

**Residual grounding limitations:** unknown/empty multi-refs still await candidate `02808424`; call-level refs, mixed percent/plain operands and generic tool percent units remain possible 100x risks. Do not label these resolved. Isolated PR #71 E2E and Asistente Casa systemd audits remain separate.

### 2026-10-13 — persistent Vibe override drift resolved

**Operator-reported PASS:** `/etc/asistente-casa-vibe.override.yml` now points to `vibe-trading:prod-8a305e91`. Owner/mode `root:zeus229 0640`; current SHA-256 begins `9323f587c6a204c1`, matching the staged replacement. Root-owned rollback backup: `/etc/asistente-casa-vibe.override.yml.bak-e5564e37-20261013` (SHA-256 begins `acc7be9135b5322f`); previous image and rollback tag remain available. The only change to the persisted override and effective Compose config is the image reference. No `compose up`, container recreation or restart; container ID `2f69e802…181e` and image `sha256:a1225cda…bea498` unchanged; healthy, restart count 0, `/live` and `/health` HTTP 200, `PPI_TRADING_DISABLED=True`. No orphan temporary override. Build worktree had already been removed.

**Deployment maintenance CLOSED** for fork code `8a305e914746a9ef78a342114730ae242d356fc8`. This is not an E2E confirmation of real portfolio response quality or Scheduled Research execution. Remaining independent engineering: unknown/empty multi-ref fail-closed candidate `02808424`; call-level percent ref limitations; fork PR #71 and Asistente Casa systemd incidents in their own lanes. This documentary update itself does not change the deployed image SHA.

### Multi-ref replacement candidate — 2026-10-13

The old `fix/upstream-derived-multi-refs@02808424` remains preserved on its original base `300c6aa6`. The replacement branch `fix/multi-derived-refs-current` reuses its six test fixtures on current fork main, adds unknown exact-field adversarial refs, and adds a narrowly scoped nine-line fail-closed guard to `grounding/policies.py::_derivation`. It rejects empty members and unresolved members of explicitly multi-source refs, plus unresolved explicit `call_id::field` refs **before** pooled market observations can accidentally satisfy the derivation. Existing exact financial proof and percentage-point logic are unchanged. This candidate is NOT yet VM validated, merged, deployed or contributed upstream. Once it is validated and merged, retire the old branch only after confirming it has no unique unmerged work.

### 2026-10-13 — multi-ref fail-closed correction integrated; deployment pending

Fast-forward fork `main` from `d47c7f4d4cdb47bde4f7531ebb33006ef156a06f` to **`d7811667b82371f66add7cb5e455a9b3cb8fae7c`** after local detached worktree validation. Operator reports 9/9 new fixtures PASS (six retained from `02808424`, three additional adverse refs), 1,919 focused grounding tests PASS (baseline 1,910), `ast.parse`, `py_compile`, `git diff --check` PASS, and no regressions. Unknown or empty multi-refs, and unresolved exact `call_id::field` refs are rejected with `no_evidence`; valid single and multi-call refs continue working. This is a nine-line guard in `policies.py`; tests in `test_grounding_multi_derived_refs.py`.

**Residual:** a lone unknown call-level ref without `::`, comma or semicolon still may validate from unrelated pooled evidence; the fix does not cover that shape. Exact indexed path syntax in the regression probe is not recognized and correctly rejects; `before::close` is supported. This remains a separate issue requiring explicit scoping/test design, not proof of globally strict ref validity. No real user E2E or production validation of this code; production is still running `8a305e914746a9ef78a342114730ae242d356fc8`. Keep original candidate `02808424` until confirming no unique work and upstream contribution disposition.

### Candidate — unknown single derived ref fail-closed (2026-10-13)

Branch `fix/unresolved-single-derived-ref`, separate from already-integrated multi-ref fix. `_derivation` rejects one nonempty unresolved bare reference with `no_evidence` rather than borrowing unrelated observed values; **observed symbols** remain valid legacy shorthand, as do resolved call, tool, field and artifact references. An empty declared ref remains subject to the existing contract. Nine additional parameterized regression cases distinguish unknown single references from valid reference categories; a valid single call may still fail for lack of both numeric operands, which is correct and different from `no_evidence`. Change limited to `policies.py` and `test_grounding_multi_derived_refs.py`. No production changes. **Pending offline VM validation:** actual symbol normalization and artifact/backtest valid references, previously integrated nine multi-ref cases, full focused grounding tests, and entity/unit/correction regressions. Do not claim PASS/merge before VM evidence.

**Validation iteration:** first run at `0c78aee2` gave 1,926 PASS / 1 FAIL: pre-existing `test_a_named_derivation_survives_prices_of_several_symbols` expects `no_symbol` for an unscoped free-text `夏普差` ref with multi-symbol market bars, but guard returned `no_evidence`. A narrow correction at `87a4f81c` skips the new early single-ref rejection when the derivation's observed market records span more than one symbol, allowing the existing `no_symbol` path. This preserves the rejection rather than updating an established reason/test. **Correction not yet VM-validated; do not merge.**

**Second validation iteration:** `152c753b` passed 1,927 grounding checks but an adversarial probe showed a lone unknown ref was still accepted with two market symbols when the response explicitly surfaced `AAPL.US (USD)`. Updated guard in `964c51bf` skips early rejection for multi-symbol inputs **only if** the claim has no resolved symbol (preserving legacy `no_symbol`), and otherwise returns `no_evidence` for unknown single refs. Added three parametrized two-symbol cases in `ab34d2a0`. This revision is **not yet locally validated**, so no merge/deploy until adversarial and full focused VM checks pass.

### 2026-10-13 — unknown single derived ref integrated, pending VM deploy

Fork `main` fast-forwarded from `b16bca4a3ef77be608397f838c3bf13a6d863849` to `31ef5d4c7554fe4332b3c078019c049c57cd00a4` after detached-VM validation. Operator reports **1,930 focused grounding tests PASS**, baseline 1,919 PASS, with 20/20 multi-ref cases PASS and existing multi-symbol `no_symbol` regression PASS; AST, compile and diff hygiene PASS. Explicit adversarial replay: with AAPL.US and MSFT.US observations, an unknown single ref (`unknown`, `ghost`, `missing_field`, `夏普差`) on a surfaced AAPL.US/USD claim now rejects with `no_evidence` (baseline incorrectly accepted); when no symbol is surfaced, legacy `no_symbol` plus surface warnings remains. Legitimate call/tool/field/symbol and exact/multi-source refs preserved. No runtime/real-data E2E validation. **Production still runs functional SHA `8a305e914746a9ef78a342114730ae242d356fc8`**; the two integrated ref fixes have not yet been deployed. Next operational action is one Vibe-only deployment, retaining rollback and private override, followed by focused smoke and health checks. Upstream proposal and original old candidate cleanup remain separate tasks.

### 2026-10-14 ART — deployment report for multi/single-reference fixes (partially closed)

Operator reports production Vibe image `vibe-trading:prod-2f437d8c` (digest `sha256:05f7e8d6da195a6c46f1691b21ebdcb9284fc795c27aca2084c9de46f46fa9db`) built from fork `main@2f437d8cfc4535ac3374cefece37ebd18ac6ebbd`, replacing `8a305e91`. Worktree tests 1,930 PASS; 20/20 multi-ref PASS; selected 182 correction/pct tests PASS. Synthetic smoke passed isolated and inside the deployed healthy container (unknown lone refs reject as `no_evidence` when instrument surfaced; unscoped multisymbol retains `no_symbol`). Health endpoints 200, no restart/OOM, `PPI_TRADING_DISABLED=True`. No real-data conversational E2E or Scheduled Research execution; those are **not** certified. Prior `prod-8a305e91` image and override kept for rollback.

**BLOCKER to operational closure:** root-owned `/etc/asistente-casa-vibe.override.yml` continues to reference `prod-8a305e91`; sudo password prevented authorized alignment. Staged updated override `/home/zeus229/vibe-override-2f437d8c-20261014.yml`, original backup `/home/zeus229/vibe-override-backups/asistente-casa-vibe.override.yml.bak-8a305e91-pre-2f437d8c-20261014T002724Z`. Before any future Compose operation, operator should compare files and change only `image:`, with privileged authorization, preserving `root:zeus229 0640`, then verify effective Compose config/health unchanged without recreating container. Separately, production checkout `/home/zeus229/vibe-trading-prod` still on `8a305e91`; can fast-forward to deployed SHA only after confirming clean checkout, no runtime dependency and exact remote target. No trading, secret or versioned code changes requested.

### 2026-10-14 ART — deployment 2f437d8c operational closure

**Operator-reported PASS, operational maintenance CLOSED.** Persisted `/etc/asistente-casa-vibe.override.yml` now references `vibe-trading:prod-2f437d8c`, owner `root:zeus229`, mode `0640`, byte-identical to staging override (SHA-256 prefix `a14177e0`); effective Compose config hashes identical. Running container preserved (start `2026-10-14T00:29:19Z`), image digest `sha256:05f7e8d6da195a6c46f1691b21ebdcb9284fc795c27aca2084c9de46f46fa9db`, healthy, zero restarts, `/live` and `/health` HTTP 200, `PPI_TRADING_DISABLED=True`. Productive checkout `/home/zeus229/vibe-trading-prod` fast-forwarded cleanly to `2f437d8cfc4535ac3374cefece37ebd18ac6ebbd` and code hash `policies.py` matches container; developer checkout and private files untouched. Previous `prod-8a305e91` image, rollback tag and prior overrides retained. No compose up or restart during closure. Grounding smoke and focused tests validated in preceding rollout, but **real conversational E2E and Scheduled Research execution remain unverified** and must not be conflated with deployment health. Next independent task: upstream PR(s) for multi-ref/single-ref failure-closed contract; don't mix PR #71 or AC systemd audits.

### 2026-10-14 ART — grounding source-ref fixes: implementation closed, upstream tracking only

**Local implementation and production deployment CLOSED.** Fork functional version `2f437d8cfc4535ac3374cefece37ebd18ac6ebbd` implements both fail-closed guards: (1) derived comma/semicolon multi-refs reject unknown/empty members, including unresolved exact `call_id::field`; (2) unknown standalone references do not borrow session-wide observations when a symbol is surfaced, while unattributed multi-symbol figures preserve `no_symbol`. Focused fork suite **1,930 PASS**, synthetic smoke in isolated and production containers PASS; runtime image `prod-2f437d8c` healthy and persistent override/checkout aligned, with rollback saved. `PPI_TRADING_DISABLED=True`. This does **not** certify real-data conversational E2E or actual Scheduled Research runs.

**Upstream contributions opened and awaiting maintainer review/CI**:
- [HKUDS/Vibe-Trading#1835](https://github.com/HKUDS/Vibe-Trading/pull/1835) — multi-ref, `zeus229:upstream/fix-derived-multi-ref-validation@a299e2576525462a06a8a87c5b1e484a3e057a12`, upstream base `54f43595597e823facc3daae095aa9d671e058e8`. New tests 9/9 PASS, focused suite 1,269 PASS vs 1,260 upstream baseline, two DCO-signed commits.
- [HKUDS/Vibe-Trading#1837](https://github.com/HKUDS/Vibe-Trading/pull/1837) — single-ref, `zeus229:upstream/fix-derived-single-ref-validation@3879131b37886ef7a332081137901aa7de5a4c4f`, same base. New tests 11/11 PASS, focused suite 1,271 PASS vs 1,260 baseline, two DCO-signed commits.
- Both were open and unmerged at last verification, with no maintainer reviews/comments. CI checks were still running at last check, with no observed failures; **CI is not declared fully passed**. Tests ran with synthetic inputs, no real-data E2E; no changes to broker, secrets, network, MCP, trading or production are needed for these PRs.
- PRs are independent and each explicitly excludes the other's scope. Retain both upstream-contribution branches until their PRs are resolved; do not force-push, delete or rewrite while open. The historical `fix/upstream-derived-multi-refs@02808424` must also be reviewed for unique work before eventual cleanup.

**Reopen criteria:** a CI failure, maintainer review requesting changes, upstream integration/rejection, or a regression found in real use. When upstream merges either PR, inspect the landed implementation and tests against fork guards before removing patches; never assume an upstream merge is already deployed. Consider a single targeted upstream-sync change only after compatibility/regression validation, then verify VM deployment separately. Keep fork PR #71 and Asistente Casa systemd services in their separate workstreams. No new code work or deployment is authorized by this documentary closure.

### 2026-10-14 ART — controlled production conversational E2E (three sessions)

**Operator-reported E2E:** Vibe functional image `2f437d8c` healthy before/after; three new authenticated API sessions against existing read-only MCP configuration; no restarts, config/secret changes or trading (`PPI_TRADING_DISABLED=True`). Runs: portfolio `20261014_025110_14_01ccd1` **PASS**; concentration `20261014_025532_95_0652db` **PASS with figure-retention observation**; derivations `20261014_025717_42_872893` **PARTIAL**. Grounding drafts were invalid in all three and corrected on the second pass to zero issues: respectively 57 / 8 / 3 `field_ref_needs_call_id` reasons plus other omissions. The original drafts repeatedly used `tool::field`; final responses used exact `call_id::field`. No fabricated numbers observed. Preserve session/run ids only, not sensitive run artifacts, in this documentation.

- Portfolio: six read-only tool calls, live PPI capture distinguished from dated canonical close, 43 exact field references after correction.
- Concentration: ACCIONES and CEDEARS Risk X-Ray returns offered `top1_weight`, `top3_weight`, `hhi`, `effective_n`, **not** `top1_pct`/`top5_pct`; ratio `0.4018` was displayed as `40.18%` appropriately. Requested top5-top1 difference not calculated due to absent fields. First draft's valid CEDEARS Top1 and HHI were omitted during correction, not re-referenced. No evidence yet of the exact canonical percent-points path in live MCP.
- Derivations: `financial_rigor` gave reciprocal of `0.0841`; `1 / 0.0841` was rejected by the >=2 operand derivation policy, and correction omitted derived `11.8906`. Final valid answer cited observed HHI and effective N, but **no end-to-end multi-ref derived figure survived**. Synthetic multi-ref regressions remain PASS; don't claim full E2E coverage.
- Session-scoped MCP tools outside persisted `agent.json enabledTools` were observed. Code inspection confirms `agent/src/tools/ppi_conversation_scope.py` intentionally extends configured Asistente Casa tools for authenticated non-Scheduled sessions, including `consultar_cartera_actual` and `consultar_riesgo_acciones_canonico`. Other reported tools (`portfolio_risk_xray`, `investor_policy`) require separate provider/discovery verification before declaring their exposure mechanism. No permission/scope changes were made.

**Prioritized follow-up (not an active deploy fix):** (1) focused one-session E2E using two available observed weight fields (e.g., `top1_weight` and `top3_weight`) to cover a genuine derived multi-ref result and its two exact refs; (2) inspect tool schema/discovery and corrected raw output to reduce `tool::field` drafting and retention loss, without broadening the grounding validator or changing privacy controls; (3) decide explicitly whether unary reciprocal `1/x` should be a separately validated scalar transform rather than loosening all derivations; (4) confirm provenance of tools outside persisted allowlist, and scheduled-session isolation. Always correct versioned code in GitHub and validate isolated before another deploy. Upstream PRs #1835 and #1837 are separate from this E2E follow-up.

### 2026-10-14 ART — targeted multi-ref E2E: data available, model self-omits derived result

Operator reports one authenticated read-only conversation `session=0347426984a7`, `run=20261014_030925_76_465cb1` using `asistente_casa_portfolio_risk_xray(asset_type=ACCIONES)` with both observed `data.concentration.top1_weight` and `top3_weight` in one call and shared `meta.position_snapshot_id`. Despite valid evidence, the agent declined to declare or publish `top3_weight - top1_weight`, claiming it could not access the actual `call_id`. Grounding consequently returned valid/zero issues on **no derived declaration**, with no correction attempt: **multi-ref conversational E2E remains INCONCLUSIVE**, not a validator PASS or FAIL. Container/code health unchanged and `PPI_TRADING_DISABLED=True`. Evidence contains private financial data; do not copy raw artifacts into GitHub.

Code review: `agent/src/agent/context.py` already explicitly requires `tool_call_id::field.path` copied verbatim. The ledger ingests provider `call_id`; this does **not alone prove** that the exact ID is visible in the model request after message assembly, compaction or provider formatting. Earlier first drafts used `tool_name::field` and corrections recovered exact refs. **Next gate before code change:** inspect a redacted record of actual model-bound messages for this run (or isolated nonproduction replay) to establish whether provider tool-call IDs, including any `call_…|fc_…` composite, were present verbatim and legible to the model; distinguish missing visibility from misunderstanding. If visible, test a minimal, general grounding instruction change; if not, repair generic message presentation/identity propagation rather than guessing or broadening validation. Maintain strict fail-closed behavior and avoid inventing source refs or requiring a real-user disclosure. Ratio example `0.7736 - 0.4018 = 0.3718` from rounded display; compute precise pp only from underlying unrounded observed values. Follow-up one-run E2E only after a focused validated change. Do not conflate with upstream PRs #1835 and #1837.

### 2026-10-14 ART — candidate: make grounding call ID visible in Codex tool output

Following E2E investigation (run `20261014_030925_76_465cb1`, original session deleted; corroborating tool-message trace taken from a separate comparable session), the grounding ledger retained the complete internal provider identity `call_…|fc_…`, but a Responses API `function_call_output` carried only the tool JSON as text; the structured protocol `call_id` represented the `call_…` segment. The model declined to publish a valid derived value because it could not identify a citation-safe source ID. `context.py` already instructs exact `call_id::field` references. This is a visibility/presentation hypothesis, **not** a confirmed defect in internal tool-call propagation.

Candidate branch `fix/codex-grounding-call-id-visibility` changes only `agent/src/providers/openai_codex.py` so its model-visible `function_call_output.output` begins with a runtime-generated single line `Grounding call ID (cite verbatim): <full internal id>`, followed by unmodified tool result text. Structured provider `call_id`, assistant function-call `id`, raw grounding ledger, persisted tool payload, other providers and auth/configuration remain unchanged. A synthetic adapter test verifies the full composite ID appears verbatim while the Responses transport still uses the first segment and the existing `fc_` item ID. **Not yet locally validated, merged, or deployed.** Gate: detached VM tests including `test_openai_codex.py`, replay/provider tests, focused grounding, validation of message parsing/size behavior and no surprise provider protocol changes. Before a real E2E, review whether adding a line ahead of JSON breaks model consumers that require JSON-only tool output or affects replay/truncation. If it does, prefer a dedicated structured model-visible envelope or alternative provider-local metadata presentation. No broad weakening of grounding or edits to trading/MCP/credentials. Keep upstream #1835/#1837 independent.

### 2026-10-14 ART — Codex grounding ID visibility: offline PASS, merged, awaiting VM deploy

Operator validated candidate `2759c9b4fc773ff58d65e90ce139c9d0fdb6675e` in a detached worktree against production baseline `2f437d8c`: **2,120 selected Codex, provider, replay/compaction and grounding tests PASS, 0 failures**, including 20/20 `test_openai_codex.py`. The sole failing test in prior candidate was fixed using `item.get("type")` for user items without `type`; no functional change since the preceding adapter code validation. Adversarial probes from the previous iteration cover structured provider ID, unmodified JSON after visible ID line, errors, large output, missing IDs, replay and parallel calls.

Merged into fork `main@8102244c1802e5f6bbf3da31b4c3754f7199201b`, preserving concurrent documentation-only commit `1958393a` with a non-forced two-parent merge. Scope: Codex adapter, its test and documentation. **Not yet deployed:** production continues on `2f437d8c`. Next: Vibe-only guarded rollout, preserve persistent private override and rollback; verify image digest/health/config and `PPI_TRADING_DISABLED=True`. After deployment, run one controlled read-only E2E asking `asistente_casa_portfolio_risk_xray(asset_type=ACCIONES)` for the top3-minus-top1 multi-ref derived figure, and verify exact source refs survive to final answer. Do not declare E2E PASS until the figure actually survives and grounding accepts it. Upstream #1835/#1837 remain independent.

### 2026-10-14 ART — E2E multi-ref after Codex call-ID fix: FAIL in derived release, ID fixed

Operator deployed fork `main@3760d7f157f8ab8cbdcacffb6ddaff1b9f1b47bf` and confirmed persisted override `/etc/asistente-casa-vibe.override.yml` matches deployed `prod-3760d7f1`, owner `root:zeus229`, mode `0640`; healthy, zero restarts, HTTP 200, `PPI_TRADING_DISABLED=True`. **Operational rollout CLOSED**, no rollback necessary.

Controlled read-only E2E: session `382f9342d2a7`, run `20261014_033341_98_0b0ace`; single `asistente_casa_portfolio_risk_xray(asset_type=ACCIONES)` call. Both ratios `data.concentration.top3_weight` and `top1_weight` present in the same canonical call/snapshot; first draft immediately used full `call_…|fc_…::field` refs, with **zero `field_ref_needs_call_id`**. This is behavioral evidence that the Codex identity-visibility change addresses the original failure; actual outbound provider request was reconstructed, not logged, so do not claim independently observed transmitted bytes.

**Full derived E2E FAIL:** first grounding pass rejected a correct subtraction due to mismatch between percent-valued declaration and prose in percentage points, plus LaTeX `{,}` / `\\%` numeric fragment detection; text-only correction retained two-source refs but changed the arithmetic representation to full-precision operands while source observed declarations remained rounded. Second pass rejected as `financial_operand_not_observed`; release redacted the derived figure (`figures_removed=1`). No invented output, no successful derived E2E. A one-round correction limit contributed to final omission but is not by itself the root cause. The reported complete ratios are ~0.7736154727382755 and 0.40176575520295116; any displayed percentage-point result must be computed against unrounded evidence.

**Code audit:** `agent/src/agent/grounding/policies.py::_derivation` already normalizes a literal outer `*100` for percent claims while preserving operand provenance. `_derived_ref_repair_metadata` requires exact observed source matching (conservative 1e-12 tolerances). It would be unsafe to globally associate operands solely by supplied refs while ignoring the numerical values; that could admit arbitrary fabricated arithmetic. Distinguish representational precision, percentage-point labeling, and LaTeX scanning with separate failing regression tests.

**Next work, independent from upstream #1835/#1837:** create synthetic reproduction from sanitized values (no private run artifacts) for each of: (a) ratio source + derived pp display/formula; (b) rounded observed display with full-precision source operands; (c) LaTeX escaped percentage and decimal braces; (d) correction retains valid observed fields and derived refs. Require baseline FAIL and candidate PASS without weakening unknown/missing/multi-source ref rejection or allowing unsupported precision. Fix in one candidate branch, revalidate targeted grounding and provider tests offline, then deploy once and run one final E2E. Do not increase correction rounds as a first-line workaround. Until then **do not close multi-ref E2E**.

### 2026-10-14 ART — grounding workstream paused by decision

**Decision: PAUSED / no new grounding fixes or E2E campaigns by default.** The owner approved stopping iterative grounding patches after the controlled `prod-3760d7f1` E2E. The deployment and persistent override are aligned, healthy, with `PPI_TRADING_DISABLED=True`. The provider call-ID visibility issue is effectively addressed: the tested E2E first draft used exact call/field refs. Existing fail-closed guards for missing single/multiple references remain deployed. Upstream PRs [#1835](https://github.com/HKUDS/Vibe-Trading/pull/1835) and [#1837](https://github.com/HKUDS/Vibe-Trading/pull/1837) remain independent and should only be followed for CI/review/merge, without expanding their scope.

**Accepted limitation:** a valid top3-minus-top1 percentage-point derived number with observed canonical refs can still be rejected due to precision/rounding, prose unit versus percent declaration, or LaTeX scanning; the published answer then omits the number rather than inventing it. This is an answer-completeness defect, **not an E2E PASS** and not evidence of false financial attribution. Preserve the reproduction under run `20261014_033341_98_0b0ace` (private evidence stays on VM). Do not weaken validation, add more repair rounds, create another branch/PR, or run another E2E just to achieve green.

**Forward policy:** Asistente Casa computes and owns deterministic, canonical portfolio numbers and suitable derived metrics where needed; Vibe consumes observed read-only values and focuses on interpretation/research. Reopen grounding only for a real report/use-case impact, unsafe acceptance of unsupported figures, confirmed upstream improvement worth adopting, or a scoped upstream maintainer request. When reopened, evaluate upstream first, reproduce with focused safe tests and keep versioned changes in GitHub; deployment/production validation remains a separate VM step. No new VM action is required by this decision.


### 2026-10-14 ART — Generic MCP keyed-metrics grounding fix merged, multi-ref remains paused

PR [#75](https://github.com/zeus229/Vibe-Trading/pull/75) merged to `main` in squash commit `6b7a4b22949a3d74fe5de22e33c4e305be30e316`, replacing the obsolete prompt-only draft PR [#71](https://github.com/zeus229/Vibe-Trading/pull/71) (closed; Codex found no unique functional code to preserve). This fix addresses **generic MCP keyed metric evidence**, symbol identity, `_pct` unit semantics, pairwise correlation axes and exact tool-call evidence for correction. It does **not** attempt the separately paused multi-ref derived-figure/LaTeX/rounding issue, and must not be used to reopen that campaign automatically.

Codex validated candidate `28ee8fa5f7bb257bfa7ea33052bdcb75f41f853c` in a detached worktree: 272 focused tests PASS, replay preserving 13 ticker risk contributions, 169 matrix cells and volatility; altered values, ambiguous refs and absent leaves rejected; no permission change. One pre-existing concurrent goal-session test fails the same way on main; `oauth_cli_kit` was not available for unrelated adapter tests. **Authenticated live E2E and deployment of merged SHA are pending**. Next operator action: deploy the integrated `main` under the normal controlled Vibe process with `PPI_TRADING_DISABLED=True`, confirm persistent override matches deployed tag, then run a single authenticated conversational ACCIONES risk E2E. Do not claim production PASS merely from the isolated replay. Any result should be documented here; no separate multi-ref campaign.


### 2026-10-14 ART — PR #75 authenticated E2E FAIL, rollback confirmed

Codex deployed/tested checkout `main@1fca4174046a8e85851a52275a2aea9d7bed3a23` with PR #75 included, isolated test home/session, single authenticated conversation. Canonical MCP called once and returned `complete` with 13 Euler contributions and 169 correlation leaves, no Risk X-Ray fallback. Visible final response retained volatility and all **13 contributions** but all **169 correlations** were rendered `(omitted※)` after two grounding failures. **Do not declare #75 E2E PASS or production deployed.** Focused checks 8/8 PASS and compile/import PASS. No full suite rerun. No secrets, PPI trades or finance SQLite changes.

Rollback completed to `vibe-trading:prod-3760d7f1`, digest prefix `sha256:05b4bc71`, healthy/zero restarts, `/live` and `/health` HTTP 200. `/etc/asistente-casa-vibe.override.yml` remains aligned to rollback, `PPI_TRADING_DISABLED=True`. Candidate `prod-1fca4174` image retained. Git checkout may be ahead of actual runtime: **do not equate `main@1fca4174` with deployed production**. Existing image-retention housekeeping mismatch remains untouched.

**Scoped follow-up:** without another deployment or broad grounding campaign, inspect only this E2E's grounding issues and figure declarations for the 13 matrix rows; distinguish ingestion/axis association, table figure parser/reference association, percent-or-ratio semantics and correction omissions. Prepare an isolated deterministic reproducer matching the failed *published text* (not only synthetically generated valid figures), compare against current upstream. Keep strict rejection and the independent multi-ref campaign paused. Only propose a minimal generic follow-up if a reproducible defect explains the 169 omissions. Do not propose upstream submission as a proven live fix before E2E PASS.


### 2026-10-14 ART — Markdown blank-corner matrix follow-up integrated, E2E pending

PR [#76](https://github.com/zeus229/Vibe-Trading/pull/76) merged into `main` as `d1f52ab57bde096a531c8ec3a4af68bf2f62e921`. Generic Markdown figure parser now recognizes complete square correlation tables with an empty upper-left corner and bold row ticker labels. Row identity is inferred only if the two axes have unique, matching ticker labels; incomplete or mismatched tables remain unscoped. This addresses the observed 169 correlation redactions after #75. **No change to unrelated multi-ref derived-number grounding**, which remains paused.

Codex validated the exact PR head `480b290476f828696663770f56aa291720df56a9` in detached worktree: **310 focused tests PASS** (one integrated replay test remained only in validation worktree, not versioned), full synthetic replay of 13 risk contributions + 169 correlations with exact refs PASS; invalid/missing refs, altered figures and malformed axes rejected. Ruff, compile, diff PASS. **Do not claim production/E2E PASS yet.** Previous candidate #75 was rolled back; live image reported `prod-3760d7f1`, healthy with trading disabled. Operator must inspect current live state and effective compose override before deployment; Git checkout alone is not runtime truth.

Next: controlled deploy of current integrated main containing #76, preserve rollback, run exactly one authenticated conversational canonical Risk ACCIONES E2E for volatility, 13 contributions and full 13×13 matrix. If successful mark production validated; if failure document precise missing figures, retain rollback and avoid restarting a broad grounding campaign.


### 2026-10-14 ART — PR #76 deployed, authenticated Risk ACCIONES E2E PASS (CLOSED)

Operator report confirms checkout `main@5fad260df5a77a17e5d85d99b77ef9a8e8a933f0`, containing functional merge `d1f52ab57bde096a531c8ec3a4af68bf2f62e921`. Actual Docker runtime **differs from the tag's historical content**: active `vibe-trading:prod-3760d7f1` now points to digest `sha256:cf2a3e748aebd658d93bec6cc225d116e9ca737993f355d6928d6638b32e9924`; previous digest `sha256:05b4bc71ac5865c55bd183f357d51857bc27738e491bf61ba57902c3697884a6` preserved as `vibe-trading:rollback-pre-pr76-3760d7f1`. Persistent /etc override unchanged and resolves the **new digest** via reused tag. For future maintenance, distinguish tag from digest and preferably give new deploys distinct tags.

Service healthy, zero restarts, HTTP 200 for `/health`, `/live` and `/`; `PPI_TRADING_DISABLED=True`. No trades or edits to production finance SQLite or private configuration.

**One authenticated E2E: PASS.** Single canonical read-only MCP call, `complete`, window 252 returns, ACCIONES 13/13 and 100% covered by weight, no trading calls. Final answer included **annualized volatility, all 13 risk contributions and all 169 correlation cells**, plus **183 exact evidence refs**. Grounding valid with **zero issues**; no redactions. Operator reported predeploy focused suite **309 PASS**. Rollback image retained, not executed. The call identity is retained only in private runtime evidence, not duplicated here.

**Scope CLOSED:** this certifies this Risk ACCIONES 252-return conversational scenario, **not** every window, other risk families or derived multi-ref arithmetic. PR #75 generic MCP keyed-evidence and PR #76 blank-corner Markdown matrices together resolve this incident. The independent multi-ref derived-figures rounding/LaTeX workstream **remains PAUSED** per preceding decision.

**Next optional engineering step, separate from production closure:** evaluate a small upstream contribution to `HKUDS/Vibe-Trading` by comparing current upstream code with fork changes from #75/#76, retaining generic fixtures and fail-closed constraints, removing fork-specific assumptions, and submitting only non-duplicated patches. No new VM deployment or grounding E2E is required merely to propose the upstream PR.

## Final grounding worktree decision — 2026-10-20

Local-agent inspection compared three dirty worktrees against `fork-ssh/main@9b352002` by symbols, tests and behavior (static review, not full runtime equivalence). `vibe-trading-main-grounding-test` (~1.6 GB) and `vibe-trading-grounding-prod-port` (~100 MB) are **retirement candidates**, not yet confirmed deleted. Their older grounding, portfolio, frontend and locale changes are reported as present or superseded in main. The old `_tool_count_pool` heuristic is not being restored: main uses declared counts. Old tail-risk vocabulary inference and the disabled local Asistente Casa tool are likewise not being restored.

The shared `~/vibe-trading` checkout must be retained (other worktrees depend on its `.git` and a VS Code terminal is active). It contains an **uncommitted GGAL corporate-entity/SEC CIK experiment**, approximately 340 lines plus nine tests, absent from fork main and upstream. Decision: **defer implementation, preserve its source and tests privately**, and keep current fail-closed instrument/entity identity behavior. Any future port requires explicit design and tests in current `identity.py`/`evidence.py`; never transplant the obsolete monolithic `grounding.py` wholesale. Do not version or publish private `.env*` files.

Before removing either retirement candidate, the local operator must verify no processes, worktree dependencies, or unique untracked content; securely handle the old worktree's private `.env`; preserve a checksummed private archive of the shared checkout's exclusive GGAL patch/tests and any unique files; and ensure the archive contains **no secrets**. This document does not authorize destructive Git commands or certify deletion. Expected recovery is ~1.7 GB, to be measured after actual cleanup.

## Grounding worktree cleanup execution — 2026-10-20 (VM report)

The local agent reported removal of `vibe-trading-grounding-prod-port` and removal of `vibe-trading-main-grounding-test` from Git worktree metadata (no `--force`, hard reset or git clean). The latter directory still contains **331 MB of root-owned `frontend/node_modules`**, so filesystem cleanup is **not complete**. Five worktrees remain: shared `~/vibe-trading`, production checkout, native-equities pilot, `vibe-cash` (PR #84), and the omission experiment under `/tmp`.

A private checksummed archive was verified at `~/vibe-archive/20261020-ggal-and-old-worktrees/` (~796 KB; restrictive permissions), including the unique uncommitted GGAL experiment and its nine tests, tracked diffs and nonsecret untracked files from both old worktrees, plus a separate `private-env/` copy of the legacy `.env`. Checksums and archive readability passed per VM report. **Do not upload this archive or its private-env to GitHub.** The old `.env` contains credentials differing from production; do not delete it or infer that it is safe to reuse without an explicit retention decision. Avoid printing any secret values.

Disk free space increased from 44,468 MiB to 45,784 MiB (~1,316 MiB), with ~331 MB further reclaimable from the residual root-owned `node_modules`. Production reported healthy; deployed image, rollback image, backups and other worktrees untouched. Final root-owned residue cleanup requires explicit operator approval and narrowly scoped elevated permissions, after path/mount verification. Update this record only after actual cleanup is confirmed.

**Follow-up operator output (2026-10-20):** The guarded command passed its directory, symlink and mount checks and reached `df -h /` after `sudo rm -rf -- "$TARGET/frontend/node_modules"`. Reported filesystem: `/dev/sda2` 117G total, 67G used, 46G available, 60% used. No error from `sudo rm` was shown. Because the subsequent `rmdir` errors were suppressed with `|| true`, complete removal of the now-empty parent directories is not yet independently confirmed; verify with `test ! -e /home/zeus229/vibe-trading-main-grounding-test` before declaring the path absent. The protected GGAL archive and other worktrees were not targeted.
