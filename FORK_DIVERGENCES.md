# Fork divergences from HKUDS/Vibe-Trading

Last upstream sync: 2026-10-09 (upstream `b1f6ce71`, fork `211a72cc`).

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

- `portfolio_summary(snapshot_id=...)` reads one immutable stored snapshot (both `extended` and
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

## Future upstream contribution bundle (deferred, 2026-10-09)

Do not open PRs until a fresh check for upstream equivalents. Two fork capabilities
introduced by `c59313b8` remain candidate *independent* upstream contributions:

1. **Reproducible technical indicators:** optional strict
   `technical_indicators(end_date=YYYY-MM-DD)`, and `read_identity` with SHA-256
   `dataset_fingerprint` of the exact input bars and provenance. A date boundary
   alone cannot guarantee identical data if a provider revises historical bars.
2. **Pinned portfolio reads:** `portfolio_summary(snapshot_id=...)` plus
   `read_identity`; a missing/incompatible snapshot fails closed as
   `snapshot_not_found`. Propose the standard upstream view only, excluding
   the fork's compact view and PPI/Asistente Casa. Verify persistence and
   immutable retrieval before claiming reproducibility.

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
