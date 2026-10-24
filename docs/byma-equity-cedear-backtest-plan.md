# BYMA equity and CEDEAR backtesting — implementation contract

Status: isolated implementation and integration tests added; **not yet runtime-verified**. **Do not remove the existing `ar_equity` fail-closed guard until implementation, source validation and tests are complete.** No bonds, ONs, bills or fixed-income engine in scope.

## Goal
Add a reusable, upstream-friendly `ArgentinaEquityEngine` for BYMA-listed common shares and CEDEARs, without depending on PPI or Asistente Casa. Preserve existing `.BA` symbol detection and Yahoo → yfinance → local provider routing. Avoid changing other markets' execution semantics.

## Existing precedents
- `agent/backtest/engines/brazil_equity.py`: dedicated B3 rule engine and config-driven fees.
- `agent/backtest/engines/india_equity.py`: delivery constraints and execution checks.
- `agent/backtest/runner.py`, `agent/backtest/engines/composite.py`: explicit Argentina fail-closed guards.
- `agent/backtest/engines/_market_hooks.py`: market and currency contracts.
- `agent/tests/test_brazil_market_routing.py`: regression precedent.

## Non-negotiable correctness requirements
1. Scope only common shares and CEDEARs, with explicit instrument classification; reject unsupported `.BA` products and unknown security classes.
2. Resolve **actual trading currency per security** (ARS vs USD counter) from trustworthy point-in-time metadata; never infer ARS from the `.BA` suffix. Reject mixed-currency portfolios unless the framework supports explicit FX conversion and multi-currency cash.
3. Distinguish exchange trading calendar from settlement calendar. Version settlement conventions by effective date; validate historical rules against BYMA official notices (including the 2024 switch from T+2 to T+1 for contado normal).
4. Require documented lot and tick sizes, trading session/venue, and fee schedules; configurable broker commissions with no invented universal defaults. Disable shorting/margin initially.
5. Data must be executable unadjusted OHLCV with declared currency, provenance and timestamp. Avoid adjusted-close fills. Reject missing/stale/inconsistent bars and look-ahead.
6. CEDEAR metadata must include underlying, conversion ratio and **effective dates of ratio changes**, splits and relevant corporate actions. Dividend cashflows require explicit currency, payment date, withholding and conversion policy. If complete event accounting is unavailable for the requested window, fail closed rather than output a misleading total return.
7. Never assume Yahoo's current CEDEAR ratio was valid historically. Official issuer/depositary and CNV disclosures are primary references; Yahoo is a price provider, not the authoritative corporate-action register.
8. No broker trading path or external side effects. Keep `PPI_TRADING_DISABLED` unaffected.

## Proposed implementation surface
- `agent/backtest/engines/argentina_equity.py` — `ArgentinaEquityEngine` inheriting `BaseEngine`.
- Explicit market-rule version table with source references and effective dates; two calendars.
- Instrument metadata resolver, corporate-action event feed and price validation; reuse existing loader registry rather than adding a proprietary dependency.
- `runner.py` and `composite.py` route `ar_equity` only once the engine's correctness gates pass; composite must not use an empty sub-engine's positions for settlement decisions.
- Regression tests for YPFD.BA, GGAL.BA, AAPL.BA, an ARS/USD counter, mixed currency, holidays, insufficient cash, fees, ratio changes, splits, dividends, and missing corporate-action metadata.
- End-to-end smoke using fixture bars; optional live Yahoo smoke is non-deterministic and must not replace fixture tests.

## Sources to verify and cite in final PR
- BYMA regulatory circulars and operating notices (calendar, settlement, lot/tick and sessions), with dated archived copies or stable links.
- Caja de Valores / CEDEAR program depositary disclosures (ratios and changes).
- CNV applicable regulation and issuer filings (corporate actions).
- Broker fee schedule only as an example configuration, not as market law.

## Exit criteria for one upstream PR
- All new and existing tests pass; reproducible example configurations for shares and CEDEARs.
- Deterministic outputs and clear unsupported-case errors.
- No dependency on private APIs, credentials, PPI or Asistente Casa.
- No bond support and no unverified historical total-return claims.


## Settlement-matched YPFD → GGAL rotation milestone (2026-10-24)

- BaseEngine now exposes default-compatible `available_cash(timestamp)` and
  `projected_reduction_buying_power(reductions, timestamp)` hooks. Non-BYMA
  engines retain the old immediate-capital behavior by default.
- BYMA evaluates buying power by **purchase settlement date**, not only
  withdrawable settled cash: an ARS T+1 sale can fund an ARS T+1 purchase
  on the same trading day. No assumption that such proceeds may be withdrawn.
- Isolated tests: `agent/tests/test_argentina_reconciliation.py` and
  `agent/tests/test_argentina_equity_primitives.py`.
- Actual `BaseEngine._execute_bars` integration fixture:
  `agent/tests/test_argentina_rotation_integration.py` (hold and rebalance,
  order independence, double-spend bounds, commission accounting).
- **Verification pending:** no pytest/CI run has yet been observed for this
  feature branch. Do not mark this milestone PASS or enable runner/composite
  routing until these tests and affected-market regression suites execute.
- Run from the repository environment:
  `pytest agent/tests/test_argentina_reconciliation.py agent/tests/test_argentina_equity_primitives.py agent/tests/test_argentina_settlement.py agent/tests/test_argentina_cash.py agent/tests/test_argentina_rotation_integration.py -q`
- Then run affected market/composite suites and the upstream CI checks.
- Remaining integration issues: terminal forced liquidation does not consult
  BYMA sale eligibility; corporate actions and CEDEAR total-return provenance
  are not complete; multi-currency netting is intentionally prohibited.
- Broker-specific pre-funding, netting permissions and trading-line variants
  are explicit limitations; do not claim identical buying power at every broker.
