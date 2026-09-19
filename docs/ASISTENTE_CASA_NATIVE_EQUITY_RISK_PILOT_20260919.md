# Asistente Casa native-equity Risk X-Ray pilot — 2026-09-19

## Purpose

Test whether the Asistente Casa portfolio behaves better when its holdings flow through Vibe's
normal Portfolio -> portfolio_summary -> portfolio_risk_xray path and Vibe fetches market
history through its generic market-data fallback chain, instead of using the custom
`source=asistente-casa` persisted-history route.

This is an experiment only. It does not change the canonical portfolio authority, trading
permissions, or production defaults.

## Why the custom path existed

The original Asistente Casa Vibe POC intentionally required Argentine instruments to use
Asistente Casa canonical identity and persisted Historical Market Data. The goal was
reproducibility and protection against symbol/venue confusion, especially:

- bare Argentine tickers being misrouted to another market;
- CEDEAR local instruments being confused with their foreign underlying;
- bonds/FCI being silently substituted by unrelated market symbols;
- local portfolio/history parity being tested against a single canonical dataset.

That design was a deliberate POC constraint, not proof that Vibe's generic market-data path
cannot analyze Argentine equities.

The dedicated `asistente_casa_portfolio_risk_xray` / `source=asistente-casa` route therefore
remains the rollback and comparison baseline.

## Pilot scope

Only these Asistente Casa `ACCIONES` are admitted:

- GGAL -> GGAL.BA
- PAMP -> PAMP.BA
- TGSU2 -> TGSU2.BA

Requirements remain fail-closed:

- broker must be `asistente-casa`;
- `source_instrument_type == ACCIONES`;
- `market == BYMA`;
- canonical `source_instrument_id` must exist;
- local ISIN must exist.

Every other Asistente Casa position is excluded from the pilot basket. No CEDEAR, bond, FCI,
cash position, or additional Argentine equity participates.

## Activation

The branch is opt-in through:

`ASISTENTE_CASA_NATIVE_EQUITY_RISK_PILOT=1`

When unset/false, the current production behavior is unchanged: bare canonical symbols plus
`source=asistente-casa` persisted history.

When enabled, `PortfolioService._risk_xray_args()` returns only the three pilot equities,
renormalized by their ARS native market values, with `.BA` symbols and no explicit source.
That causes the existing generic `portfolio_risk_xray` tool to use `source=auto` and its
normal full-history market-data loader chain.

## Validation targets

### Portfolio handoff

Confirm `portfolio_summary.context.risk_xray_args` contains exactly:

- GGAL.BA
- PAMP.BA
- TGSU2.BA

and weights sum to 1 using only those three positions.

Confirm YPFD, CEDEARs, BONOS and FCI are absent from this pilot basket.

### Market-data path

Confirm `portfolio_risk_xray` runs with generic/auto market data, not
`_fetch_asistente_casa_history`, and requests full consecutive history (`max_rows=0`).

### Previously problematic risk figures

Retest the exact derived risk families that previously suffered omitted/percentage grounding
problems:

- annualized volatility and downside deviation;
- maximum drawdown;
- VaR 95% and VaR 99%;
- Expected Shortfall / CVaR 95% and 99%;
- HHI, effective N, Top-1 and Top-3 concentration;
- diversification ratio;
- average absolute correlation and maximum-pair correlation/beta where applicable.

Check both raw decimal values and rendered percentages. Confidence labels (95/99) must remain
distinct from the metric value itself.

### Previously problematic GGAL.BA technical figures

Run a separate GGAL.BA technical/research check and verify numeric release of:

- SMA 21/42/50/200;
- RSI 14;
- MACD, signal and histogram;
- average volume 20;
- volatility 20/60;
- numeric support/resistance levels.

The prior experiment successfully fetched GGAL.BA history back to 2000 and reasoned over these
indicators, but 25 derived figures were omitted by grounding. The pilot should establish
whether the normal market-data/evidence path changes that behavior.

## Stop conditions

Stop the validation and do not merge if any of the following occurs:

- any non-pilot position enters the basket;
- an Argentine ticker is routed to `.US` or an ambiguous market;
- weights are not the renormalized ARS values of GGAL/PAMP/TGSU2;
- the generic loader cannot obtain valid consecutive history for one of the three symbols;
- the change alters behavior when the pilot environment variable is absent;
- trading capability is enabled or any order surface is touched.

## Safety

Asistente Casa/PPI remains the holdings authority. Vibe is read-only.
`PPI_TRADING_DISABLED=True` remains mandatory.
No production/private configuration is changed by this branch.
