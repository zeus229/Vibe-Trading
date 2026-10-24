# BYMA equity and CEDEAR backtesting — implementation contract

Status (2026-10-24): settlement/netting, data-audit and ratio-window gates verified locally; `.BA` routing in `runner.py`/`composite.py` is **enabled for the fork branch only**, still fail-closed without declared metadata, calendar and credit dates (see the last section). Earlier sections that say routing is disabled are historical. The unconditional `ar_equity` guard was replaced on 2026-10-24 by the engine's own fail-closed checks after the gates below passed; keep upstream routing off until the upstream PR criteria are met. No bonds, ONs, bills or fixed-income engine in scope.

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


## Yahoo/yfinance data audit and price-basis gates (2026-10-24)

Live audit of Yahoo v8 chart payloads (`events=div,splits`, 2010-2026) and the
repo's `yahoo` and `yfinance` loaders for YPFD.BA, GGAL.BA, PAMP.BA, the
CEDEARs AAPL, MSFT, KO, GOOGL, AMZN, NVDA, MELI, SPY, TSLA (.BA), the USD
lines AAPLD/GGALD/YPFDD.BA, and the US underlyings/ADRs. Scripts and raw
payloads stayed in a local scratch directory; nothing private was used.

### Findings

| Area | Finding |
|---|---|
| Currency | Yahoo declares `ARS` for every base `.BA` line and `USD` for the `D` lines. The loaders' ARS-pool contract rejects USD lines (fail closed, consistent with the single-ledger engine). |
| yfinance loader | **Bug:** `_declared_currency` required `dict`, but yfinance 1.7.0 (locked) returns a `HistoryMetadata` `Mapping`, so every LSE/BYMA/TSX symbol read as "no currency" and the whole yfinance path rejected them. Existing tests stubbed the probe. Fixed (accept `Mapping`) with a regression test. |
| Loader agreement | After the fix, `yahoo` and `yfinance` match bar for bar on the audited window (max relative close diff 4e-7, identical volume, midnight index, `quote_currency=ARS`). |
| Adjustment | OHLC is split- and dividend-adjusted (adjclose/close factor, e.g. YPFD 0.847 at the start); volume stays raw. Same caliber as other markets. |
| Share splits | Yahoo records and adjusts share splits (YPFD.BA / YPFDD.BA 10:1 on 2026-08-03): the series is continuous. |
| CEDEAR "splits" | Yahoo's CEDEAR split events are the underlying's splits (AAPL 2020 4:1, TSLA, GOOGL/AMZN 2022, NVDA 2024) and are adjusted. |
| CEDEAR ratio changes | **Not adjusted.** The implied ratio `underlying x CCL / CEDEAR` (CCL from GGAL.BA x 10 / GGAL) shows level shifts that match raw price drops: AAPL 10->20 on 2024-01-24 (AAPL.BA 25100 -> 12697.5; Yahoo only records a 2:1 "split" on AAPLD.BA), MSFT 5->10->30 (2020-10-22, 2022-11-17), GOOGL 29->58, AMZN 72->144, NVDA 3->6 (2020-10-22) and x4 on 2021-07-20. MELI.BA shows raw drops on 2020-10-22 (x0.034) and 2024-01-24 (x0.51), not verified with the implied-ratio method. KO (5), TSLA (15) and YPFD vs YPF were stable. A naive backtest books each as a 50-75% loss. |
| Placeholder bars | Many flat OHLC bars with zero volume (MSFT.BA 1640, KO.BA 1290 since 2010), including holidays (YPFD.BA 2018-01-01). They are stale, not executable prices. USD lines have hundreds of null rows (dropped by the loader). |
| Real moves | GGAL.BA/PAMP.BA fell ~47% on 2019-08-12 (PASO): genuine, must not be "corrected". |
| Minor | A few bars with high/low inconsistent with open/close (<=7 per series); not used for fills, left as is. |

The ratio values above are **inferred from prices** for the audit; official
ratios and dates must come from BYMA/Caja de Valores/CNV disclosures.

### Implemented (minimal, reusable)

- `BaseEngine.prepare_market_data(data_map)`: new hook run after loading and
  before signal generation; default returns the frames unchanged (all other
  markets unaffected). Signals and fills therefore share one price basis.
- `agent/backtest/engines/argentina_data.py` + `ArgentinaEquityEngine.prepare_market_data`:
  - require OHLCV columns, a unique ascending index and a loader-declared
    `quote_currency` equal to the instrument metadata (the `local` loader
    declares none, so it is rejected for BYMA v1);
  - drop zero-volume placeholder bars on dates outside the settlement
    calendar; a traded bar on a non-session fails closed;
  - for each declared CEDEAR ratio change inside the window, compare the
    close move between the last traded bar before and the first on/after the
    effective date with the unadjusted factor `old/new` and with ~1
    (tolerance `ar_ratio_jump_tolerance`, default 20%). Exactly one match is
    required (optional `price_series: adjusted|unadjusted` must agree);
    unadjusted evidence rescales earlier OHLC by `old/new` and volume by the
    inverse, like a split adjustment. Ambiguous (e.g. 10->11), contradicted
    or unverifiable changes fail closed;
  - reject undeclared one-bar moves beyond 40% for CEDEARs
    (`ar_max_unexplained_cedear_move`) and beyond 75% for shares
    (`ar_max_unexplained_share_move`, catches unadjusted >=4:1 splits while
    allowing the 2019 PASO move).
- Zero/missing-volume bars are not executable (`argentina_can_execute`).
- Diagnostics: `ar_price_basis = source_split_dividend_adjusted_ohlc_raw_volume`,
  `ar_cedear_ratio_adjustments`, `ar_dropped_non_session_bars`.

Performance is an **adjusted-price backtest**: dividends live in the source's
adjusted prices; no cashflow, payment date, withholding or FX entry is
created.

### Verification

| Check | Result |
|---|---|
| `test_argentina_*.py` | 75 passed (17 new in `test_argentina_price_basis.py`) |
| `test_yfinance_declared_currency.py` | 8 passed; with the old `dict` check 4 fail |
| Engines, loaders and backtest regressions (BaseEngine, India, Korea, Vietnam, Brazil, Composite, China, crypto, forex, global, UK, rebalance, `test_backtest_*`, `test_yfinance*`, `test_yahoo*`, `test_*loader*`, Argentina) | 1984 passed, 9 skipped |
| Full suite (CI ignores) | 61 failed, 21362 passed, 173 skipped; the 61 are **identical** to the pre-existing set (passes +25 = new tests) |
| ruff on changed files, CI syntax step, gates a-f | ok; gate g still fails (pre-existing, unchanged) |
| Live smoke (not a test): real Yahoo 2019-2026 frames through `prepare_market_data` | YPFD/GGAL/PAMP unchanged; every inferred ratio change for AAPL, MSFT, GOOGL, AMZN, NVDA detected as unadjusted and rescaled; KO/TSLA clean; MSFT without declared history fails closed naming 2020-10-22 and 2022-11-17 |

Key deterministic case: inlined public Yahoo AAPL.BA quotes (2024-01-15..02-02)
held through the 10->20 change return +10.5% (9 units, 10835 -> 12002.5
adjusted) instead of -45% unadjusted; without the declared change the run
aborts.

### Pending after this gate

- Official ratio histories (dates and ratios) per CEDEAR from Caja de
  Valores/BYMA; the engine trusts the declared dates and only checks prices.
- USD (`D`) lines: loaders reject them by the ARS pool contract; a separate
  USD pool/FX design is required before supporting them.
- `local` source for BYMA needs a way to declare `quote_currency`.
- Adjusted levels: like any split adjustment, pre-change price *levels* are
  restated with later information; returns are not. Lot size, tick grid and
  fixed fees apply to adjusted units (finer than real pre-change lots).
- Unadjusted share splits below 4:1 are not caught by the 75% band.
- MELI/SPY and other CEDEARs not covered by the smoke need their own ratio
  histories before use; stale-print-heavy CEDEARs (MSFT/KO.BA pre-2020) trade
  rarely in backtests because placeholder bars are not executable.
- Unchanged from earlier milestones: gate g, the 61 pre-existing suite
  failures, CI 3.11/3.14 legs, `.BA` routing disabled.


## Official ratio-event evidence and runner readiness (2026-10-24)

Official BYMA publication (2020-10-19):
https://www.byma.com.ar/newsroom/novedades-cedears
establishes **2020-10-22 ex-price**, **2020-10-23 record date** and
**2020-10-26 credit of additional certificates** for 39 changed programs.
For 2024-01-24 AAPL 10:1 -> 20:1, the CNV-authorized conversion and 2024-01-26
credit are discussed by the broker's explanatory article:
https://help.brubank.com/es/articles/8862355-cambio-de-ratios-de-cedears
This secondary description is **not a substitute** for dated issuer/depositary
instrument-level primary documentation; locate and preserve that source for
final upstream acceptance.

**Newly identified execution correctness risk:**
`argentina_data.reconcile_ratio_changes` back-adjusts OHLCV before the
ex-date and deliberately *does not change live position quantities*. That
can be a valid synthetic adjusted-unit strategy model, but it is not a
faithful ledger of deliverable CEDEAR quantities across the ex/record/credit
window. BYMA's 2020 announcement explicitly distinguishes ex-price and
credit dates. Do not allow a backtest to sell synthetic additional units on
2020-10-22 if the certificates would only be credited on 2020-10-26, absent
explicitly verified broker/clearing support.

Runner's `ar_equity` route and `CompositeEngine` remain fail-closed.
Before removing either guard:
1. Add deterministic, non-lookahead fixtures straddling each ex/record/credit
   transition (2020 and 2024), including hold-through, same-day sale, sale
   before additional credit, and acquisition after ex-date.
2. Establish and test what **adjusted price units** mean for lot size,
   tick rounding, fixed commissions, fills, position valuation and available
   certificates. Restrict crossings or report an explicit synthetic-only
   mode when trade deliverability is unproven.
3. Validate source-specific ratios and effective dates from primary
   documentation. Do not infer authoritative metadata solely from price
   discontinuities or today's ratio list.
4. Prove standalone runner and composite routing use the *same* date/cash/
   rules state and invoke the data preparation hook before signals.
5. Keep unsupported currency mixes, missing quote metadata, unverifiable
   ratio histories and insufficient calendar coverage fail-closed.
6. Run BYMA and existing-market regressions; publish an upstream-clean PR
   from an isolated change set, not the fork's unrelated history.

This gate review is a design audit, **not test execution** or release approval.


## Primary ratio sources, ex->credit window and `.BA` routing (2026-10-24)

### Official ratio evidence (issuer: Banco Comafi, issuer of these CEDEAR programs)

| Event | Document | Ex-date | Record | Credit | Ratios (old -> new) |
|---|---|---|---|---|---|
| 2020, 39 programs, RESFC-2020-20830-APN-DIR#CNV (2020-10-15) | Comafi to BCBA and holders, 2020-10-16, hosted by BYMA: `data-widgets.byma.com.ar/wp-content/uploads/2020/10/Banco-Comafi-Cambio-ratio-39-Cedears.pdf` (sha256 `99ef5d32...a4095`) | Thu 2020-10-22 | Fri 10-23 | Mon 10-26 | MSFT 5:1->10:1, GOOGL 29:1->58:1, AMZN 72:1->144:1, NVDA 12:1->24:1, MELI 2:1->60:1 (table is an image; read from the rendered page). AAPL and KO not in the list. |
| 2022, 33 programs, RESFC-2022-21967-APN-DIR#CNV (2022-10-05) | Comafi "Anuncio Fecha Efectiva", 2022-11-03: `comafi.com.ar/custodiaglobal/Multimedios/pdfs/9552.pdf` (sha256 `60547853...ec55f9`); application notice 2022-10-11 `.../9391.pdf` | Thu 2022-11-17 | Fri 11-18 | Tue 11-22 (11-21 holiday) | MSFT 10:1->30:1 |
| 2024, 30 programs, RESFC-2023-22529-APN-DIR#CNV (2023-11-29) | Comafi to BCBA, 2024-01-08: `comafi.com.ar/custodiaglobal/Multimedios/pdfs/11879.pdf` (sha256 `d14dd012...26ede12`) | Wed 2024-01-24 | Thu 01-25 | Fri 01-26 | AAPL 10:1->20:1, MELI 60:1->120:1 (and 28 others) |

The 2022 and 2024 notices call the credit date "preliminary, to be confirmed";
the ex-dates match the observed Yahoo price steps (MSFT.BA 2022-11-17,
AAPL.BA and MELI.BA 2024-01-24). The BYMA newsroom post
(`byma.com.ar/newsroom/novedades-cedears`, 2020-10-19) repeats the 2020 dates.
The broker article cited earlier is superseded by these issuer documents.
The NVDA.BA x4 step on 2021-07-20 is the US 4:1 *underlying* split, not a
ratio change; no CEDEAR notice was located, so it stays fail-closed unless a
user declares it.

Inferred-vs-official check: every inferred 2020/2022/2024 ratio matched the
issuer tables; NVDA's inferred 3->6 is 12->24 expressed in Yahoo's
split-adjusted underlying units.

### Ex-date -> credit window (implemented)

- `ratio_history` entries take `credit_date`; an in-window change without it
  fails closed. Positions opened before the ex-date may only be sold by a
  trade whose normal settlement falls on/after the credit date
  (`argentina_ratio_credit_ok`); the same rule binds the terminal close.
  BaseEngine passes no order size, so the whole position is held back, not
  only the additional certificates (conservative).
- Finding: all three official events set credit = ex + 2 sessions, and all
  happened under T+2, so an **ex-date sale settles exactly on the credit date
  and is deliverable**. The rule only binds under T+1 (from 2024-05-27) or if
  the calendar is wrong: tests show a 2022 calendar that omits the 11-21
  holiday refuses the ex-date sale, and a synthetic T+1 event (clearly marked,
  not official) defers the sale to the session that settles on the credit.
- Purchases on/after the ex-date follow normal settlement only.

### Units: adjusted prices vs real certificates (implemented)

Before an in-window change, one real certificate is `new/old` adjusted units.
`ArgentinaEquityEngine.unit_scale` drives `round_size` (whole real lots: e.g.
AAPL buys an even number of units before 2024-01-24) and `apply_slippage`
(real tick / scale: 0.5 ARS -> 0.25 on restated AAPL prices). Commissions are
invariant (same notional; one fixed fee per order). Valuation is continuous.
Trade artifacts report post-change units and prices (AAPL 2023-06-02:
2108 units at 4503.5 = 1054 certificates at 9007).
`BaseEngine` now records `_bar_ts` next to `_bar_idx` for these rules.

### Routing (implemented)

- `runner._create_market_engine`: `ar_equity` -> `ArgentinaEquityEngine`.
  Missing `ar_instruments`, calendar, credit dates, quote currency, or an
  ARS/USD mix still fail closed; `ar_settlement_sessions` accepts strict
  ISO strings (JSON config).
- `CompositeEngine`: builds the BYMA rule book and evaluates buying power,
  same-bar netting, sell/credit eligibility, terminal liquidation, data
  preparation and diagnostics against the **composite's** ledger
  (state/rules helpers shared with the standalone engine, like HOSE/India).
  `.BA` codes cannot share a composite with any other market (raises at
  construction), on top of the existing mixed-currency guard.
- Tests prove standalone and composite runs produce identical fills, equity
  and returns, in hold and rebalance modes, and that `prepare_market_data`
  runs before signals.

### Verification

| Check | Result |
|---|---|
| BYMA tests (`test_argentina_*.py`, incl. new `test_argentina_ratio_events.py`, `test_argentina_runner_composite.py`) + yfinance currency, with `--disable-socket` | 112 passed |
| Mutations: credit rule off -> 4 failures; unit scale off -> 5 failures | caught |
| Engines/loaders/runner regressions (BaseEngine, India, Korea, Vietnam, Brazil, Composite, China, crypto, forex, global, UK, rebalance, `test_backtest_*`, loaders, runner, registry, UI services) | 2128 passed, 9 skipped |
| ruff on changed files; CI syntax step; gates a-f | ok (7 ruff findings exist only in untouched files); gate g still fails (pre-existing) |
| Full suite (CI ignores) | 61 failed, 21391 passed, 173 skipped; the 61 are identical to the pre-existing set |

Real-data runs through `python -m backtest.runner` (Yahoo, 2023-06-01..2025-06-30,
10M ARS, 0.5% + 0.08% fees, 0.1% slippage; calendar = GGAL.BA traded days,
an **audit approximation**, not an official BYMA calendar):

| Run | Total return | Max DD | Trades | Notes |
|---|---|---|---|---|
| YPFD/GGAL monthly 60-day momentum rotation, hold | +175.9% | -35.7% | 7 | equal-weight benchmark +830% |
| same, rebalance | +166.9% | -35.8% | 147 | |
| AAPL.BA buy and hold, declared 2024 change | +166.7% | -33.3% | 1 | equity +1.16% on 2024-01-24 (no unit loss); benchmark +181% |
| AAPL.BA without the declared change | aborted | | | `unexplained CEDEAR price discontinuities on 2024-01-24` |

### Limitations / pending

- No official BYMA session calendar ships with the engine; users must
  supply one (the smoke used traded days). A verified calendar source is
  needed before claiming historical fidelity.
- Credit dates for 2022/2024 are the issuer's "preliminary" dates; the
  confirmation notices were not located.
- Whole-position hold during the credit window (no size-aware partial sale).
- Underlying splits that Yahoo adjusts (e.g. AAPL 2020 4:1) are not mapped to
  real lots/ticks; NVDA 2021 undeclared fails closed.
- USD (`D`) lines and composites mixing `.BA` with other markets remain
  unsupported; `local` source lacks a declared quote currency.
- Gate g, the 61 pre-existing suite failures, CI 3.11/3.14 legs and an
  upstream-clean change set remain before any upstream PR.


## First conversational attempt in production and automatic preparation (2026-10-24)

### Diagnosis (session `0eb5d346faaa`, run `20261024_051528_10_57d0a5`, trial image `byma-trial-742c489a`)

The agent recognised YPFD.BA and called `backtest` **six** times; logs show four
distinct causes, not only missing metadata:

| # | Exact error | Cause |
|---|---|---|
| 1 | `unsupported engine 'argentina_equity'` (schema) | The user named the engine; no skill said engines are selected from codes, and the schema message did not say so. |
| 2 | `ar_instruments must declare BYMA instrument metadata` | Message gave no shape. |
| 3-4 | `explicit share/cedear classification required` | Agent invented `classification`, copied PPI fields (`instrument_type`, `market`) from an Asistente Casa tool; the message never named `kind`. |
| 5 | `positive integer lot_size required` | The engine reported one field per failure: a guessing loop. |
| 6 | `positive tick_size required` | Next would have been `ar_settlement_sessions`; the agent then searched the web. |

Silent issue: the generic `commission: 0.001` was ignored by the BYMA engine,
so costs would have been zero. The fixed `tick_size` model was also wrong:
BYMA ticks are price bands.

### Official source for ticks and lots

BYMA Circular N° 3569, Anexo "Reglas de Negocio en el Segmento PPT Operaciones
de Contado", version 04-07-2024
(`data-widgets.byma.com.ar/wp-content/uploads/2024/07/BYMA-CIR3569-Anexo-v20240704.pdf`,
sha256 `811dec4c...3c19ad`): "Alteración mínima de las órdenes, Renta Variable"
bands (0.001 up to 1 ARS ... 25 for 10,000-50,000, 50 above), explicitly
including CEDEARs; standard term "Contado con Liquidación en 24 hs". The
minimum order quantity is defined only as the listing authorization's
quantity (no number), so one share per lot remains an assumption.

### Implemented

- `argentina_metadata.py`: CIR 3569 tick bands; Yahoo-descriptor
  classification (existing `yahoo_client.get_quote_summary`): "CEDEAR" text =>
  CEDEAR; untruncated name without it => share; truncated/ambiguous => unknown;
  observed-session calendar (dates with traded volume, conservative) plus a
  labelled weekday tail for final settlements.
- `ArgentinaEquityEngine`: `ar_instruments` and `ar_settlement_sessions` are
  optional. Declared values always win; missing ones are resolved per loaded
  symbol and labelled `official` / `derived` / `third_party` / `assumption`.
  Shares are completed automatically; CEDEARs are **not** (no automatic
  official ratio/credit source) and fail closed. All configuration problems
  are reported at once with exact keys and an example. Omitted `tick_size`
  uses CIR 3569 bands on the real certificate price. Generic `commission` is
  the brokerage rate unless `ar_brokerage_rate` is set; no fees is reported as
  `none_configured`.
- Reporting: scalar `ar_data_basis` (fits the tool's 2,000-char stdout tail in
  the measured run) and `artifacts/byma_preparation.json` with full provenance
  (standalone and composite).
- Runner schema message for `engine` now says market engines come from codes.
- `strategy-generate` skill documents `.BA` configs, provenance labels, and
  forbids filling metadata via web or broker tools.

### Verification

| Check | Result |
|---|---|
| BYMA tests incl. new `test_argentina_auto_prepare.py` (`--disable-socket`) | 133 passed |
| Replay: minimal config of the failed request through `runner.main` | runs first time; exact failed final config also runs |
| Engine/loader/runner/skill regressions | 3054 passed, 9 skipped (+15 `test_runtime_runner.py` only fail under `--disable-socket`; 32/32 pass normally) |
| Full suite (CI ignores) | 61 failed, 21420 passed, 173 skipped; the 61 are identical to the pre-existing set |
| Live (real Yahoo + descriptor) via `python -m backtest.runner`, YPFD.BA 2024-06-01..09-30, 1M ARS, 95%, commission 0.1% | first attempt OK: total return -4.63%, max DD -20.16%, 1 trade; 82 traded days; stdout 1,952 chars incl. `ar_data_basis` |
| Live AAPL.BA with the same minimal config | fails closed once, naming CEDEAR evidence and every key to declare |

### Limitations

- Yahoo's descriptor is third-party evidence, not an official BYMA
  classification; truncated names fail closed.
- Derived calendar can only delay settlement (missing sessions); the tail
  after the last bar does not know holidays (affects only final settlement
  dating). A declared calendar overrides it.
- CIR 3569 v2024-07-04 is applied to earlier dates as an assumption (count
  reported). Lots of one share are an assumption.
- `ar_data_basis` sits mid-output; very long stdout (e.g. 20 dropped hold
  adjustments) could push it out of the 2,000-char tail; the artifact remains.
- True conversational verification needs the image deployed; see the plan.

### Controlled deployment plan (not executed)

1. Commit/push the branch; build `vibe-trading:byma-trial-<sha12>-<ts>` from a
   clean `git archive` (as for the trial), compare against the running trial
   image (same packages; only the expected files differ) and run BYMA tests
   inside the image.
2. Backup `vibe-home` with `backup_vibe_home.py --execute` and verify hashes
   and `integrity_check`; keep the current trial image and add tag
   `rollback-pre-byma-autoprep-<date>`; prod `main` image
   (`rollback-pre-byma-trial-20261024`) stays protected.
3. Preflight a new trial override and the rollback override (current trial
   image) through `vibe_compose.sh ... config` (PPI_TRADING_DISABLED, project,
   image, volumes, port).
4. Run the same gated deploy script with automatic rollback (image, health,
   restarts, mounts, approved smoke, probe vs baseline, startup fingerprint).
5. In the UI ask: "Backtest buy-and-hold de YPFD.BA del 2024-06-01 al
   2024-09-30, 1.000.000 ARS, 95%, comisión 0,1%". Accept only if the first
   `backtest` call succeeds and the answer reports the `ar_data_basis` labels.
6. Rollback paths: to the previous trial image (new rollback override) or to
   prod `main` (`rollback_byma_trial.sh`).
