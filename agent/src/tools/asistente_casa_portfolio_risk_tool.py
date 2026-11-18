"""Risk X-Ray for the canonical Asistente Casa portfolio.

Local integration (ported from the cc54832 Vibe checkout to a5b79422):
Asistente Casa remains the source of truth for holdings and persisted market
history. Vibe performs the deterministic risk computation.

This tool is self-contained by design: it resolves its own symbols/weights
from Asistente Casa and never accepts a ``symbols`` argument from the model,
so it never trips the generic instrument-identity gate
(``agent/src/agent/instrument_identity.py`` only gates calls whose arguments
carry a key in ``code/codes/symbol/symbols/ticker/tickers/underlying/
underlyings``) and never touches the generic market-data fallback chain
(``search_symbol``, ``fetch_market_data``/``get_market_data``, yfinance,
tushare/tencent, ...).

Local customization (2026-09-15, see
``docs/investments/VIBE_TRADING_CUSTOMIZATIONS.md`` in the Asistente Casa
repo): the 120-day default lookback is a Risk X-Ray convenience default, not
a datastore boundary. There is no hard maximum lookback here anymore --
availability is whatever Asistente Casa has actually persisted. Coverage
questions ("since when do you have data") must go through
``AsistenteCasaMarketHistoryCoverageTool`` / ``/market-history/coverage``
below, never be inferred from a short history request's ``from`` date.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, timedelta
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from backtest.risk_xray import MIN_HISTORY_DAYS, compute_risk_xray
from src.agent.tools import BaseTool
from src.config.accessor import get_env_value

logger = logging.getLogger(__name__)

_DEFAULT_LOOKBACK_DAYS = 120
_WIDEN_CALENDAR_DAYS_PER_SESSION = 3
_WIDEN_CALENDAR_DAYS_BASE = 30
_SUPPORTED_ASSET_TYPES = {"ACCIONES", "CEDEARS"}
#: Joint ACCIONES + CEDEARS basket, analyzed as ONE portfolio (one capture, one
#: panel, one native ``compute_risk_xray`` call). BONOS, FCI and cash stay out of
#: the statistics and are reported as out-of-scope weight.
_COMBINED_SCOPE = "ACCIONES+CEDEARS"
_COMBINED_TYPES = ("ACCIONES", "CEDEARS")
_ARS = "ARS"
_SUPPORTED_HORIZONS = {"1y", "ytd", "since_inception"}
_TIMEOUT_SECONDS = 30.0
_NUMERIC_FORMAT = "json_number"
_MIN_RISK_OBSERVATIONS = 30
_MIN_PARTIAL_WEIGHT_COVERAGE = 0.90


class ConnectorError(RuntimeError):
    """Raised when Asistente Casa cannot return a trustworthy payload."""


def _select_risk_panel(
    closes_raw: pd.DataFrame,
    weights: Mapping[str, float],
    *,
    min_observations: int = _MIN_RISK_OBSERVATIONS,
    min_weight_coverage: float = _MIN_PARTIAL_WEIGHT_COVERAGE,
) -> tuple[pd.DataFrame, dict[str, float], dict[str, Any]]:
    """Select a strict full-basket panel, or an explicit high-coverage fallback.

    Full-basket strict intersection remains the primary path. If a newly added
    holding has too little canonical history, symbols are removed deterministically
    from shortest individual history to longest until the common panel reaches the
    minimum observation floor. The fallback is accepted only while at least 90% of
    original sleeve value remains represented. Nothing is forward-filled or
    synthesized, and excluded holdings stay explicit in metadata.
    """
    if closes_raw.empty:
        raise ValueError("historical close panel is empty")
    original_symbols = list(closes_raw.columns)
    strict = closes_raw.dropna(axis=0, how="any")
    if len(strict) >= min_observations:
        return strict, dict(weights), {
            "coverage_mode": "strict_full_basket",
            "included_symbols": original_symbols,
            "excluded_symbols": [],
            "included_weight_pct": 100.0,
            "excluded_weight_pct": 0.0,
            "min_observations": min_observations,
            "min_weight_coverage_pct": round(min_weight_coverage * 100.0, 4),
        }

    ordered = sorted(
        original_symbols,
        key=lambda symbol: (
            int(closes_raw[symbol].notna().sum()),
            float(weights.get(symbol, 0.0)),
            symbol,
        ),
    )
    included = list(original_symbols)
    excluded: list[str] = []
    panel = strict
    for symbol in ordered:
        if len(included) <= 2:
            break
        included.remove(symbol)
        excluded.append(symbol)
        coverage = sum(float(weights.get(item, 0.0)) for item in included)
        if coverage < min_weight_coverage:
            break
        panel = closes_raw[included].dropna(axis=0, how="any")
        if len(panel) >= min_observations:
            normalized = {
                item: float(weights[item]) / coverage
                for item in included
            }
            return panel, normalized, {
                "coverage_mode": "partial_high_coverage",
                "included_symbols": included,
                "excluded_symbols": excluded,
                "included_weight_pct": round(coverage * 100.0, 4),
                "excluded_weight_pct": round((1.0 - coverage) * 100.0, 4),
                "min_observations": min_observations,
                "min_weight_coverage_pct": round(min_weight_coverage * 100.0, 4),
            }

    coverage = sum(float(weights.get(item, 0.0)) for item in included)
    raise ValueError(
        "insufficient common historical coverage for Risk X-Ray: "
        f"full_basket_common={len(strict)}, best_common={len(panel)}, "
        f"included_weight_pct={coverage * 100.0:.4f}, "
        f"required_observations={min_observations}, "
        f"required_weight_coverage_pct={min_weight_coverage * 100.0:.2f}"
    )


def _position_value(position: Mapping[str, Any]) -> float:
    value = position.get("market_value_ars")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(
            f"position {position.get('symbol')!r} has no finite market_value_ars; "
            "the joint basket cannot be weighted"
        )
    return float(value)


def _split_combined_positions(
    positions: list[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[Mapping[str, Any]]]:
    """Keep ACCIONES + CEDEARS from one whole-portfolio capture and reweight them.

    The weights are market value over the combined ACCIONES+CEDEARS value, taken
    from the same capture. Everything else (BONOS, FCI) is returned separately so
    it can be reported as out of scope, never silently dropped.
    """
    in_scope: list[dict[str, Any]] = []
    out_of_scope: list[Mapping[str, Any]] = []
    for position in positions:
        kind = str(position.get("instrument_type") or "").strip().upper()
        if kind in _COMBINED_TYPES:
            in_scope.append(dict(position))
        else:
            out_of_scope.append(position)
    total = sum(_position_value(position) for position in in_scope)
    if total <= 0:
        raise ValueError("the ACCIONES+CEDEARS basket has no positive market value")
    for position in in_scope:
        position["weight_scope"] = _position_value(position) / total
    return in_scope, out_of_scope


def _assert_ars_series(
    series: Mapping[str, Any], symbols: list[str], *, strict: bool
) -> None:
    """Refuse series whose declared currency is not ARS (returns must be comparable).

    ``strict`` (the joint basket) also refuses a series that declares no currency
    at all, because mixing an undeclared series with ARS ones is exactly the case
    this guard exists for. Single-scope calls keep their historical behaviour.
    """
    for symbol in symbols:
        item = series.get(symbol)
        if not isinstance(item, Mapping):
            continue
        declared = {str(item.get("currency") or "").strip().upper()}
        for row in item.get("observations") or []:
            if isinstance(row, Mapping):
                declared.add(str(row.get("currency") or "").strip().upper())
        declared.discard("")
        foreign = declared - {_ARS}
        if foreign:
            raise ValueError(
                f"{symbol} history is not in ARS (declared: {sorted(foreign)}); "
                "refusing to mix currencies in one basket"
            )
        if strict and not declared:
            raise ValueError(f"{symbol} history does not declare its currency; refusing to assume ARS")


def _window_sources(
    series: Mapping[str, Any], symbols: list[str], first: pd.Timestamp, last: pd.Timestamp
) -> dict[str, dict[str, dict[str, int]]]:
    """Count observation sources and quality flags inside the analysed window."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for symbol in symbols:
        sources: dict[str, int] = {}
        quality: dict[str, int] = {}
        item = series.get(symbol)
        for row in (item.get("observations") if isinstance(item, Mapping) else None) or []:
            if not isinstance(row, Mapping):
                continue
            stamp = pd.Timestamp(row["date"])
            if stamp < first or stamp > last:
                continue
            source = str(row.get("source") or "unknown")
            sources[source] = sources.get(source, 0) + 1
            flag = str(row.get("quality") or "unknown")
            quality[flag] = quality.get(flag, 0) + 1
        out[symbol] = {"sources": sources, "quality": quality}
    return out


def _bridged_returns(
    closes: pd.DataFrame, raw: pd.DataFrame
) -> list[dict[str, Any]]:
    """Returns whose two closes are separated by a raw date that was dropped.

    The strict shared-date panel can pair two non-adjacent closes when a symbol
    lacks an interior bar; that return spans several sessions. Each one is listed
    with the skipped date(s) and the symbol(s) lacking a bar there, so it is
    identified rather than hidden.
    """
    dates = list(closes.index)
    details: list[dict[str, Any]] = []
    for before, after in zip(dates[:-1], dates[1:]):
        skipped = raw.index[(raw.index > before) & (raw.index < after)]
        if len(skipped):
            missing = sorted(
                str(column) for column in raw.columns if raw.loc[skipped, column].isna().any()
            )
            details.append(
                {
                    "return_from": before.date().isoformat(),
                    "return_to": after.date().isoformat(),
                    "skipped_dates": [stamp.date().isoformat() for stamp in skipped],
                    "symbols_missing_on_skipped_dates": missing,
                }
            )
    return details


def _env_credentials() -> tuple[str, str]:
    base_url = str(get_env_value("ASISTENTE_CASA_BASE_URL") or "").strip().rstrip("/")
    api_key = str(get_env_value("ASISTENTE_CASA_API_KEY") or "").strip()
    if not base_url or not api_key:
        raise ValueError("Asistente Casa environment is not configured")
    return base_url, api_key


def _get(base_url: str, api_key: str, path: str, params: Mapping[str, str]) -> dict[str, Any]:
    query = urlencode(params)
    request = Request(
        f"{base_url}{path}?{query}",
        headers={"Accept": "application/json", "X-Vibe-API-Key": api_key},
        method="GET",
    )
    try:
        with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise ConnectorError(f"Asistente Casa returned HTTP {exc.code} for {path}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ConnectorError(f"Asistente Casa is unavailable ({path}): {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"Asistente Casa returned invalid JSON for {path}") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ConnectorError(f"Asistente Casa returned an unsuccessful payload for {path}")
    if payload.get("numeric_format") != _NUMERIC_FORMAT:
        raise ConnectorError(f"unexpected numeric_format for {path}; refusing localized numeric parsing")
    return payload


def _fetch_coverage(
    base_url: str, api_key: str, *, symbols: list[str], asset_type: str
) -> dict[str, Any]:
    """Read persisted-only coverage metadata (no OHLC rows) for a basket.

    This is the only sanctioned source for "since when do you have data"
    answers -- it must never be approximated from a short history request's
    ``from`` date.
    """
    payload = _get(
        base_url,
        api_key,
        "/inversiones/vibe/market-history/coverage",
        {"symbols": ",".join(symbols), "asset_type": asset_type},
    )
    if payload.get("policy") != "persisted_only":
        raise ValueError("Asistente Casa coverage policy is not persisted_only")
    return payload


class AsistenteCasaPortfolioRiskXrayTool(BaseTool):
    """Analyze the user's canonical Asistente Casa investment portfolio."""

    name = "asistente_casa_portfolio_risk_xray"

    description = (
        "PREFERRED for VaR, Expected Shortfall, diversification ratio and other "
        "Risk X-Ray-only metrics, and for CEDEARS risk. For ACCIONES "
        "volatility, correlation matrix, Euler risk contributions and exactly "
        "63/126/252/504/756 daily returns, prefer the MCP tool "
        "mcp_asistente_casa_consultar_riesgo_acciones_canonico when available "
        "in authenticated conversational sessions; preserve its returned figures "
        "without recomputing or merging unlike methodologies. "
        "Use this INSTEAD of the generic "
        "portfolio_risk_xray whenever the request is about 'mi cartera real', "
        "'mis datos reales', or the account already loaded through the "
        "asistente-casa connector: this tool resolves holdings, weights and "
        "canonical persisted price history itself — the model does NOT supply "
        "symbols or weights. For requests such as 'últimas N ruedas' / 'last N "
        "trading sessions', pass lookback_sessions=N exactly; do not translate N "
        "into calendar days. The model must NOT call search_symbol/fetch_market_data/"
        "get_market_data first. Validated for ACCIONES (Argentine "
        "equities), CEDEARS, and the joint basket asset_type='ACCIONES+CEDEARS' "
        "(one portfolio, ARS closes, one capture). "
        "IMPORTANT ROUTING RULES: this result is deterministic for the same "
        "portfolio snapshot and date range. Call this tool at most ONCE per "
        "scope for a given analysis unless the user explicitly requests a "
        "different date range or refreshed data. Do NOT call it repeatedly to "
        "verify metrics it already returned. Full-basket strict intersection is "
        "preferred; when a recent holding has too little persisted history, the "
        "tool may return an explicitly labeled partial_high_coverage result only "
        "if at least 90% of sleeve value and 30 common observations remain. Do NOT "
        "use ACCIONES or CEDEARS "
        "metrics as if either scope represented the complete portfolio, and do NOT "
        "merge or average the numbers of two single-scope calls: ask for "
        "asset_type='ACCIONES+CEDEARS' instead. The result lists included and "
        "excluded instruments and the share of the total portfolio covered "
        "(meta.coverage). Do NOT extrapolate these results to BONOS or FCI."
    )

    parameters = {
        "type": "object",
        "properties": {
            "asset_type": {
                "type": "string",
                "enum": ["ACCIONES", "CEDEARS", "ACCIONES+CEDEARS"],
                "description": (
                    "Portfolio asset type. Validated scopes: ACCIONES, CEDEARS, and "
                    "ACCIONES+CEDEARS (joint ARS equity basket)."
                ),
            },
            "start_date": {
                "type": "string",
                "description": (
                    "Optional YYYY-MM-DD start date. Defaults to 120 calendar days "
                    "before end_date. Mutually exclusive with horizon and lookback_sessions. This default "
                    "is a Risk X-Ray convenience window, not the start of persisted "
                    "history -- use asistente_casa_market_history_coverage for "
                    "'since when do you have data' questions."
                ),
            },
            "end_date": {
                "type": "string",
                "description": "Optional YYYY-MM-DD end date. Defaults to today.",
            },
            "lookback_sessions": {
                "type": "integer",
                "minimum": 2,
                "description": (
                    "Optional exact number of most recent shared persisted trading sessions "
                    "to analyze, ending at end_date. Dynamic: use the user-requested value "
                    "(for example 21, 42, 63, 126). Mutually exclusive with start_date and "
                    "horizon. Sessions are selected only after strict cross-symbol calendar "
                    "alignment; no weekends/holidays are synthesized and no forward-fill is used."
                ),
            },
            "horizon": {
                "type": "string",
                "enum": ["1Y", "YTD", "since_inception"],
                "description": (
                    "Optional shorthand instead of start_date. '1Y' = trailing 365 "
                    "calendar days. 'YTD' = January 1 of end_date's year. "
                    "'since_inception' = this basket's earliest common persisted "
                    "date (from the coverage endpoint), i.e. the earliest date for "
                    "which every symbol in scope has an aligned observation -- use "
                    "this for 'analyze since inception' portfolio-level requests. "
                    "Mutually exclusive with start_date and lookback_sessions."
                ),
            },
        },
        "required": [],
    }

    repeatable = True
    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        return bool(
            str(get_env_value("ASISTENTE_CASA_BASE_URL") or "").strip()
            and str(get_env_value("ASISTENTE_CASA_API_KEY") or "").strip()
        )

    def execute(self, **kwargs: Any) -> str:
        try:
            return self._run(**kwargs)
        except Exception as exc:  # noqa: BLE001 -- tool must always return JSON
            logger.warning("asistente_casa_portfolio_risk_xray failed: %s", exc)
            return json.dumps(
                {"status": "error", "error": str(exc), "source": "asistente-casa"},
                ensure_ascii=False,
                allow_nan=False,
            )

    def _run(self, **kwargs: Any) -> str:
        asset_type = str(kwargs.get("asset_type") or "ACCIONES").strip().upper().replace(" ", "")
        combined = asset_type == _COMBINED_SCOPE
        if asset_type not in _SUPPORTED_ASSET_TYPES and not combined:
            raise ValueError(
                f"asset_type {asset_type!r} is not validated; currently supported: "
                f"ACCIONES, CEDEARS, {_COMBINED_SCOPE}"
            )
        scope_types = _COMBINED_TYPES if combined else (asset_type,)

        start_raw = kwargs.get("start_date")
        end_raw = kwargs.get("end_date")
        horizon_raw = kwargs.get("horizon")
        lookback_raw = kwargs.get("lookback_sessions")
        horizon = str(horizon_raw).strip().lower().replace("-", "_") if horizon_raw else None
        if horizon and horizon not in _SUPPORTED_HORIZONS:
            raise ValueError(f"horizon {horizon_raw!r} is not supported; use 1Y, YTD, or since_inception")
        if lookback_raw is not None:
            if isinstance(lookback_raw, bool) or not isinstance(lookback_raw, int):
                raise ValueError("lookback_sessions must be an integer")
            if lookback_raw < 2:
                raise ValueError("lookback_sessions must be at least 2")
            lookback_sessions: int | None = lookback_raw
        else:
            lookback_sessions = None
        if horizon and start_raw:
            raise ValueError("start_date and horizon are mutually exclusive")
        if lookback_sessions is not None and (start_raw or horizon):
            raise ValueError("lookback_sessions is mutually exclusive with start_date and horizon")

        base_url, api_key = _env_credentials()

        # ------------------------------------------------------------
        # Canonical portfolio, scoped to this asset_type
        # ------------------------------------------------------------
        # The joint basket reads the whole portfolio ONCE (``ALL``) and filters it
        # here, so every weight comes from a single capture; two per-scope reads
        # could value the two sleeves at different instants.
        portfolio_payload = _get(
            base_url, api_key, "/inversiones/vibe/portfolio",
            {"asset_type": "ALL" if combined else asset_type},
        )
        portfolio_block = portfolio_payload.get("portfolio", {})
        all_positions = portfolio_block.get("positions", [])
        if combined:
            positions, out_of_scope = _split_combined_positions(all_positions)
        else:
            positions, out_of_scope = all_positions, []
        if not positions:
            raise ValueError(f"Asistente Casa returned no positions for {asset_type}")

        symbols = [str(position["symbol"]).strip().upper() for position in positions]
        if len(set(symbols)) != len(symbols):
            raise ValueError("Asistente Casa returned a symbol in more than one instrument type")
        weights = {
            str(position["symbol"]).strip().upper(): float(position["weight_scope"])
            for position in positions
        }
        type_by_symbol = {
            str(position["symbol"]).strip().upper(): (
                str(position.get("instrument_type") or "").upper() or None
            )
            for position in positions
        }
        symbols_by_type = {
            kind: [s for s in symbols if (type_by_symbol[s] or asset_type) == kind]
            for kind in scope_types
        }

        end = date.fromisoformat(str(end_raw)) if end_raw else date.today()
        coverage_earliest_common: date | None = None
        if lookback_sessions is not None:
            earliest_dates: list[date] = []
            for kind in scope_types:
                if not symbols_by_type[kind]:
                    continue
                coverage = _fetch_coverage(
                    base_url, api_key, symbols=symbols_by_type[kind], asset_type=kind
                )
                # The broadest start bounds the fetch; it must NOT be the basket's
                # earliest COMMON date, which a single recent holding (NU, PFE)
                # pulls to last week and would starve every longer window. Short
                # histories are handled by the explicit partial-coverage fallback.
                earliest_raw = coverage.get("earliest_any_date") or coverage.get("earliest_common_date")
                if not earliest_raw:
                    raise ValueError(
                        "Asistente Casa coverage has no earliest_common_date for this basket"
                    )
                earliest_dates.append(date.fromisoformat(str(earliest_raw)))
            coverage_earliest_common = min(earliest_dates)
            estimated_calendar_days = max(
                _DEFAULT_LOOKBACK_DAYS, lookback_sessions * 2 + 14
            )
            start = max(
                coverage_earliest_common,
                end - timedelta(days=estimated_calendar_days),
            )
        else:
            start = self._resolve_start(
                start_raw=start_raw,
                horizon=horizon,
                end=end,
                symbols=symbols,
                asset_type=asset_type,
                base_url=base_url,
                api_key=api_key,
                symbols_by_type=symbols_by_type if combined else None,
            )
        if start >= end:
            raise ValueError("start_date must be before end_date")
        start_date, end_date = start.isoformat(), end.isoformat()

        instrument_names: dict[str, str | None] = {}
        portfolio_positions: list[dict[str, Any]] = []
        for position in positions:
            symbol = str(position["symbol"]).strip().upper()
            raw_name = str(position.get("name") or "").strip()
            name = raw_name or None
            instrument_names[symbol] = name
            portfolio_positions.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "label": f"{symbol} - {name}" if name else symbol,
                    "weight_scope": float(position["weight_scope"]),
                }
            )

        weight_sum = sum(weights.values())
        if weight_sum <= 0:
            raise ValueError("portfolio weights sum to zero")

        # ------------------------------------------------------------
        # Canonical persisted market history, daily EOD only
        # ------------------------------------------------------------
        def _fetch_history(from_date: str, to_date: str) -> dict[str, Any]:
            """Read persisted EOD history per instrument type and merge it.

            Each type is requested with its own ``asset_type`` so identity
            resolution stays strict; the merged payload keeps the single-scope
            shape the rest of this method (and its tests) already consume.
            """
            merged_series: dict[str, Any] = {}
            complete = True
            last: dict[str, Any] = {}
            for kind in scope_types:
                kind_symbols = symbols_by_type[kind]
                if not kind_symbols:
                    continue
                payload = _get(
                    base_url,
                    api_key,
                    "/inversiones/vibe/market-history",
                    {"symbols": ",".join(kind_symbols), "asset_type": kind, "from": from_date, "to": to_date},
                )
                if payload.get("policy") != "persisted_only":
                    raise ValueError("Asistente Casa market-history policy is not persisted_only")
                if str(payload.get("interval") or "").upper() != "1D":
                    raise ValueError("Asistente Casa market-history interval is not 1D")
                unresolved = list(payload.get("unresolved_symbols") or [])
                unsafe = list(payload.get("unsafe_symbols") or [])
                if unresolved or unsafe:
                    raise ValueError(
                        "Asistente Casa historical coverage has unresolved/unsafe identities: "
                        f"unresolved={unresolved}, unsafe={unsafe}"
                    )
                _assert_ars_series(payload.get("series") or {}, kind_symbols, strict=combined)
                complete = complete and bool(payload.get("complete"))
                merged_series.update(payload.get("series") or {})
                last = payload
            return {**last, "series": merged_series, "complete": complete}

        history_payload = _fetch_history(start_date, end_date)
        history_response_complete = bool(history_payload.get("complete"))

        # N daily returns need N+1 closes: the first close only anchors the first
        # return. ``lookback_sessions`` therefore counts RETURNS, as the Asistente
        # Casa MCP risk tool (63/126/252 returns) does.
        min_observations = (
            lookback_sessions + 1 if lookback_sessions is not None else _MIN_RISK_OBSERVATIONS
        )

        def _panel(payload: Mapping[str, Any]):
            series: dict[str, pd.Series] = {}
            for symbol in symbols:
                observations = payload.get("series", {}).get(symbol, {}).get("observations", [])
                series[symbol] = pd.Series(
                    {pd.Timestamp(row["date"]): float(row["close"]) for row in observations},
                    name=symbol,
                    dtype=float,
                )
            raw = pd.DataFrame(series).sort_index()
            if raw.empty:
                raise ValueError("historical close panel is empty")
            # strict_intersection_no_fill remains the primary path. If the full
            # basket has too few common dates, allow only an explicit high-value
            # partial fallback; never forward-fill or synthesize history.
            selected, selected_weights, meta = _select_risk_panel(
                raw, weights, min_observations=min_observations
            )
            return raw, selected, selected_weights, meta

        def _try_panel(payload: Mapping[str, Any]):
            try:
                return _panel(payload)
            except ValueError as exc:
                if lookback_sessions is None or "insufficient common" not in str(exc):
                    raise
                return None

        first_panel = _try_panel(history_payload)
        if first_panel is not None:
            closes_raw, closes, risk_weights, coverage_meta = first_panel
        if lookback_sessions is not None and first_panel is None and coverage_earliest_common is not None:
            # No usable panel at all (not even the explicit partial fallback):
            # widen ONCE, bounded to 3*N+30 calendar days before end. A usable
            # partial panel is never widened -- a wider range cannot add history
            # to short-history symbols (NU, PFE) but would cross corporate-action
            # events (CEDEAR ratio changes) that Asistente Casa's identity guard
            # rejects by design. That guard is deliberately not caught here.
            wide_start = max(
                coverage_earliest_common,
                end - timedelta(days=_WIDEN_CALENDAR_DAYS_PER_SESSION * lookback_sessions + _WIDEN_CALENDAR_DAYS_BASE),
            )
            if wide_start < start:
                start = wide_start
                start_date = start.isoformat()
                history_payload = _fetch_history(start_date, end_date)
                history_response_complete = bool(history_payload.get("complete"))
                first_panel = _try_panel(history_payload)
                if first_panel is not None:
                    closes_raw, closes, risk_weights, coverage_meta = first_panel
        if first_panel is None:
            available = len(
                pd.DataFrame(
                    {
                        symbol: pd.Series(
                            {
                                pd.Timestamp(row["date"]): float(row["close"])
                                for row in history_payload.get("series", {})
                                .get(symbol, {})
                                .get("observations", [])
                            },
                            dtype=float,
                        )
                        for symbol in symbols
                    }
                ).dropna(axis=0, how="any")
            )
            raise ValueError(
                f"requested {lookback_sessions} daily returns ({lookback_sessions + 1} shared "
                f"closes) but only {available} shared closes are available for {asset_type}"
            )

        missing_by_symbol = {
            symbol: int(count) for symbol, count in closes_raw.isna().sum().items() if count
        }
        full_basket_common_date_count = len(closes_raw.dropna(axis=0, how="any"))
        dropped_non_common_dates = len(closes_raw) - len(closes)
        common_date_count_before_window = len(closes)
        if lookback_sessions is not None:
            closes = closes.tail(lookback_sessions + 1)

        # ------------------------------------------------------------
        # Vibe deterministic Risk X-Ray
        # ------------------------------------------------------------
        explicit_min_history = (
            min(MIN_HISTORY_DAYS, lookback_sessions)
            if lookback_sessions is not None
            else MIN_HISTORY_DAYS
        )
        report = compute_risk_xray(
            closes,
            risk_weights,
            periods_per_year=252,
            min_history=explicit_min_history,
        )
        if lookback_sessions is not None and lookback_sessions < MIN_HISTORY_DAYS:
            report.setdefault("warnings", []).append(
                f"explicit lookback_sessions={lookback_sessions} is shorter than "
                f"the default {MIN_HISTORY_DAYS}-session history filter; short-window "
                "risk estimates may be less stable"
            )

        included_set = set(coverage_meta["included_symbols"])
        return_observations = int(report["inputs"]["return_observations"])
        # Only dates/symbols that are part of the analysed basket: an excluded
        # short-history symbol must neither create nor explain a skipped date.
        raw_included = closes_raw[[s for s in symbols if s in included_set and s in closes_raw]].dropna(how="all")
        bridged_details = _bridged_returns(closes, raw_included)
        bridged = len(bridged_details)
        if bridged:
            report.setdefault("warnings", []).append(
                f"{bridged} of {return_observations} returns span more than one session because a "
                "symbol lacks an interior bar (listed in meta.coverage.bridged_return_details); "
                "they are kept as observed and not synthesized"
            )
        window_sources = _window_sources(
            history_payload.get("series", {}),
            [symbol for symbol in symbols if symbol in included_set],
            closes.index[0],
            closes.index[-1],
        )
        source_totals: dict[str, int] = {}
        quality_totals: dict[str, int] = {}
        for item in window_sources.values():
            for key, value in item["sources"].items():
                source_totals[key] = source_totals.get(key, 0) + value
            for key, value in item["quality"].items():
                quality_totals[key] = quality_totals.get(key, 0) + value

        def _pct_total(position: Mapping[str, Any]) -> float | None:
            weight = position.get("weight_total_portfolio")
            return round(100.0 * float(weight), 4) if isinstance(weight, (int, float)) and not isinstance(weight, bool) else None

        position_by_symbol = {str(pos["symbol"]).strip().upper(): pos for pos in positions}
        included_rows: list[dict[str, Any]] = []
        excluded_rows: list[dict[str, Any]] = []
        for symbol in symbols:
            position = position_by_symbol[symbol]
            row = {
                "symbol": symbol,
                "instrument_type": type_by_symbol[symbol] or asset_type,
                "weight_total_portfolio_pct": _pct_total(position),
                "weight_scope_pct": round(100.0 * weights[symbol] / weight_sum, 4),
                "valid_closes_in_fetched_history": int(closes_raw[symbol].count()) if symbol in closes_raw else 0,
            }
            if symbol in included_set:
                included_rows.append(row)
            else:
                row["reason"] = "insufficient_common_history_for_requested_window"
                excluded_rows.append(row)
        out_of_scope_rows = [
            {
                "symbol": str(pos.get("symbol") or "").strip().upper(),
                "instrument_type": str(pos.get("instrument_type") or "").strip().upper() or None,
                "weight_total_portfolio_pct": _pct_total(pos),
                "reason": "no_adequate_statistical_series_in_scope",
            }
            for pos in out_of_scope
        ]

        def _sum(rows: list[dict[str, Any]]) -> float | None:
            values = [row["weight_total_portfolio_pct"] for row in rows]
            return None if any(value is None for value in values) else round(sum(values), 4)

        covered_pct = _sum(included_rows)
        excluded_pct = _sum(excluded_rows)
        out_pct = _sum(out_of_scope_rows)
        all_weight_pct = [_pct_total(pos) for pos in all_positions]
        cash_other_pct = (
            None if any(value is None for value in all_weight_pct)
            else round(max(0.0, 100.0 - sum(all_weight_pct)), 4)  # type: ignore[arg-type]
        )
        def _value(position: Mapping[str, Any]) -> float | None:
            value = position.get("market_value_ars")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                return None
            return float(value)

        def _value_sum(items: list[Mapping[str, Any]]) -> float | None:
            values = [_value(item) for item in items]
            return None if any(value is None for value in values) else round(sum(values), 2)

        total_value = portfolio_block.get("total_value_ars")
        total_value = float(total_value) if isinstance(total_value, (int, float)) and not isinstance(total_value, bool) else None
        included_positions = [position_by_symbol[row["symbol"]] for row in included_rows]
        excluded_positions = [position_by_symbol[row["symbol"]] for row in excluded_rows]
        scope_value = _value_sum(list(positions))
        denominators = {
            "total_portfolio_value_ars": total_value,
            "scope_value_ars": scope_value,
            "included_value_ars": _value_sum(included_positions),
            "excluded_value_ars": _value_sum(excluded_positions),
            "out_of_scope_value_ars": _value_sum(list(out_of_scope)),
            "definitions": {
                "covered_pct_of_total_portfolio": "included_value_ars / total_portfolio_value_ars (total includes cash and every instrument type)",
                "excluded_pct_of_total_portfolio": "excluded_value_ars / total_portfolio_value_ars",
                "out_of_scope_pct_of_total_portfolio": "out_of_scope_value_ars / total_portfolio_value_ars",
                "cash_and_other_pct_of_total_portfolio": "100 - sum of every position's weight over total_portfolio_value_ars",
                "covered_pct_of_scope": "included weight / scope weight (scope = " + asset_type + ")",
                "weight_scope_pct": "position value / scope_value_ars",
            },
        }
        quality_notes = [
            "Constant-weight historical model over persisted ARS EOD closes; not account performance or True TWR.",
        ]
        if coverage_meta["coverage_mode"] != "strict_full_basket":
            quality_notes.append("Excluded instruments are part of the real portfolio; these metrics do not describe their risk.")
        if quality_totals.get("close_only"):
            quality_notes.append(
                f"{quality_totals['close_only']} window bars are close_only (provider Open/High/Low inconsistent); only the close is used."
            )
        if bridged:
            quality_notes.append(f"{bridged} multi-session returns (see warnings).")
        coverage_report = {
            "scope": asset_type,
            "currency": _ARS,
            "included": included_rows,
            "excluded_from_statistics": excluded_rows,
            "out_of_scope": out_of_scope_rows,
            "covered_pct_of_total_portfolio": covered_pct,
            "excluded_pct_of_total_portfolio": excluded_pct,
            "out_of_scope_pct_of_total_portfolio": out_pct,
            "cash_and_other_pct_of_total_portfolio": cash_other_pct,
            "covered_pct_of_scope": coverage_meta["included_weight_pct"],
            "denominators": denominators,
            "return_observations": return_observations,
            "first_close_date": closes.index[0].date().isoformat(),
            "first_return_date": closes.index[1].date().isoformat(),
            "last_date": closes.index[-1].date().isoformat(),
            "bridged_returns": bridged,
            "bridged_return_details": bridged_details,
            "history_sources": source_totals,
            "history_quality": quality_totals,
            "per_symbol_sources": window_sources,
            "quality_notes": quality_notes,
        }

        result = {
            "status": "ok",
            "source": "asistente-casa",
            "asset_type": asset_type,
            "portfolio_positions": portfolio_positions,
            "instrument_names": instrument_names,
            "data": report,
            "meta": {
                "portfolio_contract_version": portfolio_payload.get("contract_version"),
                "history_contract_version": history_payload.get("contract_version"),
                "position_quantity_source": portfolio_payload.get("position_quantity_source"),
                "position_snapshot_id": portfolio_payload.get("position_snapshot_id"),
                "position_snapshot_at": portfolio_payload.get("position_snapshot_at"),
                "history_policy": history_payload.get("policy"),
                "history_source_selection": history_payload.get("source_selection"),
                "horizon": (
                    "lookback_sessions"
                    if lookback_sessions is not None
                    else (horizon or "default_120d")
                ),
                "lookback_sessions": lookback_sessions,
                "start_date": (
                    closes.index[0].date().isoformat()
                    if lookback_sessions is not None
                    else start_date
                ),
                "end_date": (
                    closes.index[-1].date().isoformat()
                    if lookback_sessions is not None
                    else end_date
                ),
                "history_fetch_start_date": start_date,
                "requested_end_date": end_date,
                "symbols": symbols,
                "position_count": len(symbols),
                "risk_position_count": len(coverage_meta["included_symbols"]),
                **coverage_meta,
                "total_value_ars": portfolio_block.get("total_value_ars"),
                "scope_value_ars": (
                    denominators["scope_value_ars"] if combined else portfolio_block.get("scope_value_ars")
                ),
                "weight_sum": weight_sum,
                "risk_weight_sum": sum(risk_weights.values()),
                "history_complete": history_response_complete,
                "risk_scope_complete": coverage_meta["coverage_mode"] == "strict_full_basket",
                "raw_close_observations": len(closes_raw),
                "full_basket_common_date_count": full_basket_common_date_count,
                "full_basket_dropped_non_common_dates": len(closes_raw) - full_basket_common_date_count,
                "close_observations": len(closes),
                "return_observations": return_observations,
                "coverage": coverage_report,
                "common_date_count": len(closes),
                "common_date_count_before_window": common_date_count_before_window,
                "dropped_non_common_dates": dropped_non_common_dates,
                "missing_dates_by_symbol": missing_by_symbol,
                "alignment_policy": "strict_intersection_no_fill_with_explicit_high_coverage_fallback",
                "coverage_disclaimer": (
                    None if coverage_meta["coverage_mode"] == "strict_full_basket"
                    else "Risk metrics cover only the included high-history positions; excluded holdings remain part of the real portfolio and these metrics must not be described as 100% sleeve risk."
                ),
            },
        }
        return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)

    @staticmethod
    def _aligned_close_panel(
        history_payload: Mapping[str, Any],
        symbols: list[str],
    ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int], int]:
        """Build the strict shared-session close panel from persisted history."""
        series: dict[str, pd.Series] = {}
        for symbol in symbols:
            observations = (
                history_payload.get("series", {})
                .get(symbol, {})
                .get("observations", [])
            )
            if not observations:
                raise ValueError(f"no historical observations for {symbol}")
            series[symbol] = pd.Series(
                {
                    pd.Timestamp(row["date"]): float(row["close"])
                    for row in observations
                },
                name=symbol,
                dtype=float,
            )

        closes_raw = pd.DataFrame(series).sort_index()
        if closes_raw.empty:
            raise ValueError("historical close panel is empty")

        missing_by_symbol = {
            symbol: int(count)
            for symbol, count in closes_raw.isna().sum().items()
            if count
        }
        closes = closes_raw.dropna(axis=0, how="any")
        if closes.empty:
            raise ValueError("historical close panel has no common dates across symbols")
        dropped_non_common_dates = len(closes_raw) - len(closes)
        return closes_raw, closes, missing_by_symbol, dropped_non_common_dates

    @staticmethod
    def _resolve_start(
        *,
        start_raw: Any,
        horizon: str | None,
        end: date,
        symbols: list[str],
        asset_type: str,
        base_url: str,
        api_key: str,
        symbols_by_type: Mapping[str, list[str]] | None = None,
    ) -> date:
        """Resolve the effective start date for the requested window.

        No calendar-day maximum is enforced here: actual availability is
        whatever Asistente Casa has persisted, and the strict basket
        intersection downstream already fails closed instead of padding or
        inventing observations when coverage falls short of what was asked.
        """
        if start_raw:
            return date.fromisoformat(str(start_raw))
        if horizon == "1y":
            return end - timedelta(days=365)
        if horizon == "ytd":
            return date(end.year, 1, 1)
        if horizon == "since_inception":
            groups = (
                [(kind, syms) for kind, syms in symbols_by_type.items() if syms]
                if symbols_by_type
                else [(asset_type, symbols)]
            )
            earliest: list[date] = []
            for kind, syms in groups:
                coverage = _fetch_coverage(base_url, api_key, symbols=syms, asset_type=kind)
                earliest_common = coverage.get("earliest_common_date")
                if not earliest_common:
                    raise ValueError(
                        "Asistente Casa coverage has no earliest_common_date for this basket"
                    )
                earliest.append(date.fromisoformat(str(earliest_common)))
            return max(earliest)
        return end - timedelta(days=_DEFAULT_LOOKBACK_DAYS)


class AsistenteCasaMarketHistoryCoverageTool(BaseTool):
    """Report persisted historical coverage for the Asistente Casa portfolio.

    This tool answers "since when do you have data" / "until when do you have
    quotes" questions using ``/inversiones/vibe/market-history/coverage``
    directly -- it never downloads OHLC rows and never infers datastore
    inception from a Risk X-Ray short-window request. Like the risk tool
    above, it resolves its own symbols from the current Asistente Casa
    portfolio and never accepts a ``symbols`` argument from the model, so it
    does not trip the generic instrument-identity gate.
    """

    name = "asistente_casa_market_history_coverage"

    description = (
        "Report persisted historical DATA COVERAGE (not risk metrics) for the "
        "user's CURRENT Asistente Casa portfolio: since when data exists, "
        "until when it is up to date, and how many observations are stored. "
        "Use this INSTEAD of asistente_casa_portfolio_risk_xray whenever the "
        "user asks 'since when', 'how far back', 'until when', 'how much "
        "history do you have' -- for one asset_type scope (ACCIONES or "
        "CEDEARS) or, if asset_type is omitted, for both. Do NOT answer "
        "coverage questions from a Risk X-Ray result's start_date: that is "
        "only a short default request window, not the start of the "
        "datastore. The response distinguishes, per scope, the OLDEST "
        "INDIVIDUAL persisted date across the basket (earliest_any_date, and "
        "per-symbol first_date in series) from the COMMON basket date every "
        "symbol shares (earliest_common_date) -- report both when the user "
        "asks about a group ('mis acciones', 'mis CEDEARs') and only the "
        "per-symbol first_date when the user asks about one instrument."
    )

    parameters = {
        "type": "object",
        "properties": {
            "asset_type": {
                "type": "string",
                "enum": ["ACCIONES", "CEDEARS"],
                "description": (
                    "Optional portfolio scope. Omit to report coverage for both "
                    "ACCIONES and CEDEARS."
                ),
            },
        },
        "required": [],
    }

    repeatable = True
    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        return bool(
            str(get_env_value("ASISTENTE_CASA_BASE_URL") or "").strip()
            and str(get_env_value("ASISTENTE_CASA_API_KEY") or "").strip()
        )

    def execute(self, **kwargs: Any) -> str:
        try:
            return self._run(**kwargs)
        except Exception as exc:  # noqa: BLE001 -- tool must always return JSON
            logger.warning("asistente_casa_market_history_coverage failed: %s", exc)
            return json.dumps(
                {"status": "error", "error": str(exc), "source": "asistente-casa"},
                ensure_ascii=False,
                allow_nan=False,
            )

    def _run(self, **kwargs: Any) -> str:
        asset_type_raw = kwargs.get("asset_type")
        if asset_type_raw:
            asset_type = str(asset_type_raw).strip().upper()
            if asset_type not in _SUPPORTED_ASSET_TYPES:
                raise ValueError(
                    f"asset_type {asset_type!r} is not validated; currently supported: "
                    "ACCIONES, CEDEARS"
                )
            scopes = [asset_type]
        else:
            scopes = sorted(_SUPPORTED_ASSET_TYPES)

        base_url, api_key = _env_credentials()

        scope_results: dict[str, Any] = {}
        for scope in scopes:
            portfolio_payload = _get(base_url, api_key, "/inversiones/vibe/portfolio", {"asset_type": scope})
            positions = portfolio_payload.get("portfolio", {}).get("positions", [])
            if not positions:
                continue
            symbols = [str(position["symbol"]).strip().upper() for position in positions]
            coverage_payload = _fetch_coverage(base_url, api_key, symbols=symbols, asset_type=scope)
            scope_results[scope] = {
                "symbols": symbols,
                "earliest_any_date": coverage_payload.get("earliest_any_date"),
                "earliest_common_date": coverage_payload.get("earliest_common_date"),
                "latest_common_date": coverage_payload.get("latest_common_date"),
                "complete": coverage_payload.get("complete"),
                "unresolved_symbols": coverage_payload.get("unresolved_symbols"),
                "symbols_without_history": coverage_payload.get("symbols_without_history"),
                "series": coverage_payload.get("series"),
            }

        if not scope_results:
            raise ValueError(f"Asistente Casa returned no positions for {scopes}")

        all_first_dates = [
            str(item["first_date"])
            for scope in scope_results.values()
            for item in scope["series"].values()
            if item.get("first_date")
        ]
        all_last_dates = [
            str(item["last_date"])
            for scope in scope_results.values()
            for item in scope["series"].values()
            if item.get("last_date")
        ]

        result = {
            "status": "ok",
            "source": "asistente-casa",
            "policy": "persisted_only",
            "scopes": scope_results,
            "overall": {
                "earliest_any_date": min(all_first_dates) if all_first_dates else None,
                "latest_any_date": max(all_last_dates) if all_last_dates else None,
            },
        }
        return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
