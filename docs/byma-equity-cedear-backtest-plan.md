# BYMA equity and CEDEAR backtesting — implementation contract

Status: settlement/netting milestone **verified locally on 2026-10-24** (see the milestone section); `.BA` routing in `runner.py`/`composite.py` remains **disabled**. **Do not remove the existing `ar_equity` fail-closed guard until implementation, source validation and tests are complete.** No bonds, ONs, bills or fixed-income engine in scope.

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
5. Price adjustment and execution units must follow an **explicit, consistent convention**: Vibe's existing Yahoo/yfinance path uses split/dividend-adjusted OHLCV. Do not mix adjusted prices with unadjusted share counts, point-in-time lot/tick schedules or nominal fees without validating the outcome. Require declared quote currency, adjustment provenance and timestamp. Reject missing/stale/inconsistent bars and look-ahead.
6. CEDEAR metadata must identify underlying and **effective-dated conversion ratio history**. Validate representative ratio/split transitions and price/quantity consistency without assuming Yahoo's adjusted OHLCV covers every CEDEAR event. If unsupported changes occur in the requested interval, fail closed with a clear explanation. A full dividend cashflow ledger (payment dates, taxes, FX, withholding) is **out of scope for v1**; do not label an adjusted-price simulation as broker-realized cashflows.
7. Never assume Yahoo's current CEDEAR ratio was valid historically. Official issuer/depositary and CNV disclosures are primary references; Yahoo is a price provider, not the authoritative corporate-action register.
8. No broker trading path or external side effects. Keep `PPI_TRADING_DISABLED` unaffected.

## Proposed implementation surface
- `agent/backtest/engines/argentina_equity.py` — `ArgentinaEquityEngine` inheriting `BaseEngine`.
- Explicit market-rule version table with source references and effective dates; two calendars.
- Instrument metadata and adjusted-price provenance validation; reuse existing Yahoo/yfinance/local loader registry rather than adding a proprietary dependency. No full corporate-action cashflow engine in v1.
- `runner.py` and `composite.py` route `ar_equity` only once the engine's correctness gates pass; composite must not use an empty sub-engine's positions for settlement decisions.
- Regression tests for YPFD.BA, GGAL.BA, AAPL.BA, ARS/USD counters, mixed currency, holidays, insufficient cash, fees, adjusted-vs-unadjusted price consistency, and known CEDEAR ratio/split changes. Dividends are tested as adjusted-series behavior, not cash distributions.
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
- Terminal forced liquidation now consults the market's sell rule through
  `BaseEngine.can_liquidate_at_end(symbol, timestamp)` (default `True`, so
  every other market is unchanged). BYMA refuses the end-of-backtest sale of
  a position whose newest opening fill has not settled: the position stays
  open, no `end_of_backtest` trade/fill is invented, the final snapshot keeps
  `positions > 0`, and equity values it at what the close would net
  (last close, slippage/tick, exit commission). `_engine_diagnostics()`
  declares `ar_unliquidated_position_count`, `ar_unliquidated_positions`,
  `ar_terminal_equity_basis` and `ar_unsettled_sale_receivable_at_end`
  (terminal equity includes proceeds that settle after the last bar).

### Local verification evidence (2026-10-24)

Environment: dedicated worktree of `feat/byma-equity-cedear-backtest`
(base `8d4b0a4b`), Python 3.12.3 venv with `pip install -e ".[dev]"`
(CI uses 3.11/3.14 and extra `openbb,stats,anthropic,matrix`). No PPI,
Asistente Casa, broker or private service involved; fixtures are synthetic.

Defects found and fixed:

1. `ArgentinaEquityEngine.__init__` bound a local `date` while validating
   CEDEAR ratio history, shadowing `datetime.date`; every engine constructed
   with `ar_settlement_sessions` raised `NameError`. This made 9 of the 11
   initial failures (all settlement/rotation tests had never run). Locals
   renamed (also in `ratio_at`).
2. `test_rotation_includes_all_trade_fees_in_final_equity` called
   `_engine(fee=5)`, which the helper did not accept. Helper now maps `fee`
   to `ar_fixed_fee` (plus optional rates, slippage, tick, calendar).
3. Terminal liquidation bypassed BYMA sale eligibility (reproduced: buy on
   the last bar and the same-day `end_of_backtest` sale with a fake round
   trip). Fixed as described above.

Results:

| Check | Result |
|---|---|
| `test_argentina_*.py` before fixes | 11 failed, 34 passed |
| `test_argentina_*.py` after fixes | 58 passed |
| Engine/market regressions: `test_base_engine`, India, Korea, Vietnam, Brazil routing, `test_composite_*`, market hooks, China A/futures, crypto, forex, global equity/futures, robustness, metrics JSON, rebalance, UK, `test_backtest_*`, Argentina | 1017 passed, 1 skipped |
| `ruff check` on changed/Argentina files | clean |
| `tools/ci_grep_gates.sh` | gates a–f ok; **gate g fails** (see pending) |
| `tools/test_ci_*_gate.py` | 39 passed |
| CI syntax step (compileall/py_compile incl. `backtest/runner.py`) | ok |
| Full suite (CI ignores) on this branch | 61 failed, 21337 passed, 173 skipped |
| Full suite on unmodified `8d4b0a4b` | 72 failed, 21313 passed, 173 skipped |
| Difference | the 11 Argentina failures fixed here; the other 61 are **identical** on both trees |
| 9 MCP-registry failures among the 61 | pass in isolation (36/36): order-dependent registry pollution, pre-existing |

New integration coverage in `test_argentina_rotation_integration.py`
(each in `hold` and `rebalance`): T+2 position not rotatable the next day
and no funds used twice; rotation across the 2024-05-27 T+2→T+1 cutover;
terminal liquidation blocked for unsettled positions, with exit-cost-netted
equity; settled positions still liquidated and receivable declared; ledger
identity (initial cash ± every fill's notional and fee, with proportional and
fixed fees, slippage and tick rounding) equals final capital and equity; full
`run_backtest` pipeline reports the retained position. Mutation checks:
making `can_liquidate_at_end` always true fails 5 tests; making same-bar
reductions provide no buying power fails 5 tests.

Maturity compatibility: under the normal-cash rule table, a later trade can
never settle before an earlier sale (lag only drops 2→1 at the cutover, and
5/24 T+2 and 5/27 T+1 both settle 5/28), so the "sale settles after the
purchase" guard in `available_for_settlement` is defense in depth, tested at
unit level (`test_t2_sale_cannot_fund_an_earlier_settling_buy`,
`test_t1_sale_cannot_fund_same_day_cash_settlement`). Mixed ARS/USD is still
rejected at construction.

### Real pending items

- `tools/ci_grep_gates.sh` gate g rejects tracked internal documents on this
  branch: `AGENTS.md`, `docs/ASISTENTE_CASA_EQUITY_RISK_GROUNDING_HANDOFF.md`,
  this plan, and two `docs/sessions/` notes. Must be resolved (untrack or
  move to `wiki/docs/`) before any upstream PR; the branch also carries
  ~895 fork commits ahead of `origin/main` that need to be isolated.
- The 61 pre-existing full-suite failures (grounding preservation/formula/
  repair, README engine counts, OU missing-lag pairs, order-dependent MCP
  registry tests) are not from this milestone but block a green CI on this
  branch.
- Not yet run: CI's Python 3.11/3.14 legs and optional extras.
- The settlement calendar must cover the settlement date of the last bar;
  otherwise the run fails closed (`settlement calendar does not cover`).
- Retained terminal positions are valued, not sold; no trade-level stats
  (win rate, holding time) include them. Readers must check
  `ar_unliquidated_position_count`.
- Same-day buy-and-sell (intraday netting) of one instrument is blocked
  conservatively; scale-ins block partial sells until the newest lot settles.
- Point-in-time CEDEAR ratio effects on positions and price/quantity consistency
  are not yet validated; multi-currency netting is prohibited. A complete
  corporate-action/dividend cash ledger is deferred beyond v1.
- Broker-specific pre-funding, netting permissions and trading-line variants
  are explicit limitations; do not claim identical buying power at every broker.
- `.BA` routing in `runner.py`/`composite.py` stays disabled until the above
  data/event contracts exist.


## V1 scope simplification: adjusted-price backtesting (2026-10-24)

Review of upstream market engines found no per-market dividend/split cash ledger
in the Brazil, Vietnam, India or Korea rule engines. The shared Yahoo client
uses a split-adjusted quote series and an adjclose/close factor for dividend
adjustment, while yfinance downloads with `auto_adjust=True`. BYMA v1 should
**reuse these existing price loaders**, not introduce an independent event-ledger
framework that would diverge from upstream.

Before enabling .BA routing, complete these narrower gates:

1. Audit exactly what Yahoo and yfinance deliver for representative .BA shares
   and CEDEARs, including whether adjustment, currency and volume units agree.
2. Show synthetic price/quantity/fee tests for a split and a CEDEAR ratio change;
   the execution must not create artificial gain/loss from unit rescaling.
3. Explicitly reject a ratio transition when historical metadata or price
   conventions cannot prove consistent quantities and values; no look-ahead.
4. Describe performance as **adjusted-price backtest**, not realized cash
   dividend accounting. No inferred payment dates, withholding, dividend FX
   conversions or tax entries. Preserve the existing BYMA fail-closed route
   until the consistency tests and affected-market regressions pass.

Deferred: general event feed, dividend payment/withholding ledger and broker-
faithful taxable total-return reconstruction. Those may become reusable upstream
features later, but are not prerequisites for a transparent V1 comparable to
existing market engines.
