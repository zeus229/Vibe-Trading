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

## Experimental branches kept (not in `main`)

| Branch | Unique value | Why not merged |
|---|---|---|
| `feat/asistente-casa-provider-identity` | trusted ISIN-verified provider identity locking `.BA` symbols; allow-listed backtest stdout metrics as grounded evidence; microcompact preservation of named evidence | written against the Sept-20 grounding core; overlaps heavily with the current one, needs a clean port plus new tests |
| `feat/read-freshness-context-recovery` | pinned/fresh portfolio snapshot reads by `snapshot_id`; dataset fingerprint/read identity for technical indicators; replay-lease consumption | large (22 commits) and touches `loop.py`, portfolio store and tools |
| `exp/ac-native-equities-risk-xray-20260919` | native-equities risk X-Ray pilot with `.BA` history routing and pilot document | experimental; worktree `vibe-native-equity-pilot-20260919` |

Retired branches are archived as tags `archive/<date>/<branch>` on the fork.
