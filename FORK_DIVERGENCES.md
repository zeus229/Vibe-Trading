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
