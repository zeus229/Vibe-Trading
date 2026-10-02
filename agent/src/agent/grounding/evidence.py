"""Evidence intake: what a tool result is allowed to ground.

Every observed number the gate validates against enters here. Kind maps are
keyed on tool field names, never on natural language.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from src.agent.grounding.identity import (
    _CANONICAL_SYMBOL_RE,
    _infer_currency,
    _infer_venue,
    _normalize_symbol,
    _utc_now,
)
from src.portfolio.iso4217 import is_iso_currency

_PRICE_FIELDS = {"open", "high", "low", "close", "adj_close", "price"}

_TIMESTAMP_FIELDS = ("trade_date", "date", "datetime", "timestamp", "time", "index")

_MAX_GENERIC_EVIDENCE = 2_000

# Only these CSV columns count; Volume, Adj Close etc. are ignored so the
# contradiction check gains no values it would be willing to accept.
_CSV_PRICE_COLUMNS = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "price": "price",
}

_CSV_DATE_COLUMNS = {"date", "datetime", "trade_date", "timestamp", "index"}

_CSV_FILENAME_SUFFIX_MAP = (
    ("_V", ".V"),
    ("_TO", ".TO"),
    ("_F", "=F"),
    ("_US", ".US"),
)

# Nested quote leaves of non-OHLC tools ("data.last", "quote[0].close_price")
# mapped to canonical price fields. Only unambiguous quote fields: ratios,
# volumes, strikes and analyst targets stay out so the check accepts no more.
_GENERIC_PRICE_FIELD_ALIASES = {
    "open": "open",
    "open_price": "open",
    "openprice": "open",
    "开盘": "open",
    "开盘价": "open",
    "high": "high",
    "high_price": "high",
    "最高": "high",
    "最高价": "high",
    "low": "low",
    "low_price": "low",
    "最低": "low",
    "最低价": "low",
    "close": "close",
    "close_price": "close",
    "closeprice": "close",
    "prev_close": "close",
    "pre_close": "close",
    "preclose": "close",
    "previous_close": "close",
    "收盘": "close",
    "收盘价": "close",
    "昨收": "close",
    "adj_close": "adj_close",
    "adjclose": "adj_close",
    "adjusted_close": "adj_close",
    "price": "price",
    "last": "price",
    "last_price": "price",
    "lastprice": "price",
    "latest_price": "price",
    "current_price": "price",
    "market_price": "price",
    "settle": "price",
    "settlement": "price",
    "settle_price": "price",
    "vwap": "price",
    "现价": "price",
    "最新价": "price",
}

# Metric family per field name: a figure is grounded only by evidence of its
# own kind, so an observed price never stands in for a volatility (#1336).
_ANALYSIS_KIND_ALIASES = {
    "annualized_vol": "vol",
    "annualized_volatility": "vol",
    "volatility": "vol",
    "return_vol": "vol",
    "return_volatility": "vol",
    "vol": "vol",
    "max_drawdown": "drawdown",
    "maxdd": "drawdown",
    "drawdown": "drawdown",
    "sharpe": "sharpe",
    "sharpe_ratio": "sharpe",
    "win_rate": "win_rate",
    "hit_rate": "win_rate",
    "hitrate": "win_rate",
    "probability": "probability",
    "prob": "probability",
    "total_return": "return",
    "annual_return": "return",
    "cumulative_return": "return",
    "benchmark_return": "return",
    "excess_return": "return",
    "annualized_return": "return",
    "return": "return",
    "returns": "return",
    "ic_positive_ratio": "win_rate",
    # Portfolio co-movement leaves a risk x-ray tool emits under its own
    # field names (asistente_casa_portfolio_risk_xray's correlation/
    # diversification block). Grouped as one kind since the ledger only
    # needs "this is a legitimate risk metric", never a cross-kind identity
    # check between e.g. beta and avg pairwise correlation.
    "diversification_ratio": "diversification",
    "avg_pairwise_abs": "correlation",
    "avg_pairwise_correlation": "correlation",
    "beta_to_equal_weight": "correlation",
    "effective_n": "concentration",
    "hhi": "concentration",
    "top1_weight": "concentration",
    "top3_weight": "concentration",
    "downside_deviation_annualized": "vol",
    "var": "tail_risk",
    "var_95": "tail_risk",
    "var_99": "tail_risk",
    # quantlib_call records a scalar result under the function name, and "var"
    # alone matches only a whole leaf (_EXACT_ONLY_ALIASES), so these two need
    # their own entries or a real VaR result grounds nothing (#1464).
    "historical_var": "tail_risk",
    "parametric_var": "tail_risk",
    "cvar": "tail_risk",
    "cvar_95": "tail_risk",
    "cvar_99": "tail_risk",
    "es": "tail_risk",
    "es_95": "tail_risk",
    "es_99": "tail_risk",
    "expected_shortfall": "tail_risk",
    # Chinese TOOL FIELD NAMES from A-share tools, not answer prose.
    "最大回撤": "drawdown",
    "回撤": "drawdown",
    "夏普": "sharpe",
    "夏普比率": "sharpe",
    "年化波动率": "vol",
    "波动率": "vol",
    "胜率": "win_rate",
    "命中率": "win_rate",
    "概率": "probability",
    "年化收益率": "return",
    "累计收益率": "return",
    "收益率": "return",
}

# Sample-size, window and duration leaves share a metric's token but hold a
# count: ``return_observations = 81`` is 81 observations, never an 81% return
# (#1420/#1426). Matched on the leaf's first and last field-name token.
_METADATA_COUNT_HEADS = frozenset({"n"})

_METADATA_COUNT_TAILS = frozenset(
    {"obs", "observations", "window", "lookback", "count", "days", "duration"}
)

# Money-denominated fields a currency-marked figure may quote besides a price:
# a market-data row's amount, and the backtest engine's equity and P&L leaves.
_AMOUNT_FIELDS = frozenset(
    {
        "amount",
        "turnover",
        "成交额",
        "final_value",
        "initial_cash",
        "initial_capital",
        "pnl",
        "total_pnl",
        "avg_pnl",
        "notional",
    }
)

# The backtest engine's summary outputs, relative to its run directory: small
# documents of scalars, recorded whole when the backtest completes. A list
# inside them (1,000 Monte Carlo Sharpe samples, one entry per rebalance) is a
# series and is not descended. metrics.csv/json are recorded as they always
# were; every other file must appear, byte for byte, in the run card's
# artifact manifest, which the engine writes after its outputs.
_BACKTEST_SUMMARY_FILES = (
    "run_card.json",
    "artifacts/metrics.csv",
    "artifacts/metrics.json",
    "artifacts/risk_xray.json",
    "artifacts/validation.json",
    "artifacts/rebalance_notes.json",
)

_MANIFEST_EXEMPT = frozenset({"run_card.json", "artifacts/metrics.csv", "artifacts/metrics.json"})

# Tools whose result names a file the model itself wrote. Such a file is never
# engine output, whatever its name.
_WRITE_TOOLS = frozenset({"write_file", "edit_file"})

# Columns of a backtest table that name the row's instrument (trades.csv "code").
_TABLE_SYMBOL_COLUMNS = frozenset({"symbol", "code", "ticker"})

#: Written by the loop into the active run when it archives a backtest there;
#: ``source_run`` names the run directory the copy came from.
ARCHIVE_MANIFEST = ".archived_backtest.json"


def _relative_posix(path: Path, root: Path) -> str:
    """``path`` relative to ``root`` in POSIX form, "" for ``root`` itself."""
    try:
        relative = Path(path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return Path(path).as_posix()
    return "" if relative == "." else relative


def _file_sha256(path: Path) -> str | None:
    """Hex SHA-256 of a file's bytes, or None when it cannot be read."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _archive_source(root: Path) -> str | None:
    """The run directory name the active run's archived backtest came from."""
    try:
        payload = json.loads((root / ARCHIVE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    source = payload.get("source_run") if isinstance(payload, dict) else None
    return str(source) if source else None


def _run_card_manifest(directory: Path) -> dict[str, str]:
    """``{relative path: sha256}`` from a backtest's run card, or empty."""
    try:
        card = json.loads((directory / "run_card.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    entries = card.get("artifacts") if isinstance(card, dict) else None
    if not isinstance(entries, list):
        return {}
    return {
        str(entry["path"]): str(entry["sha256"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("path") and entry.get("sha256")
    }


def _summary_scalars(path: Path) -> list[tuple[str, int | float]]:
    """``(field, value)`` for every scalar of a summary artifact; lists are not descended.

    A CSV contributes its first data row by (casefolded) header, a JSON
    document every number reachable through objects alone, under its dotted
    path ("tail_risk.var_95").
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    pairs: list[tuple[str, int | float]] = []
    if path.suffix == ".csv":
        try:
            rows = list(csv.reader(text.splitlines()))
        except csv.Error:
            return []
        if len(rows) < 2:
            return []
        for name, cell in zip(rows[0], rows[1]):
            value = _coerce_csv_number(cell)
            if name.strip() and value is not None:
                pairs.append((name.strip().casefold(), value))
        return pairs
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    def visit(item: Any, dotted: str) -> None:
        if _is_number(item):
            pairs.append((dotted, item))
        elif isinstance(item, dict):
            for key, child in item.items():
                visit(child, f"{dotted}.{key}" if dotted else str(key))

    visit(data, "")
    return pairs


def _cell_symbol(text: str) -> str | None:
    """The canonical symbol a table cell or header consists of, or None."""
    stripped = (text or "").strip()
    if not stripped or _CANONICAL_SYMBOL_RE.fullmatch(stripped) is None:
        return None
    return _normalize_symbol(stripped)

# Generic tools do not share one schema, but a money value still has a stable
# contract: an ISO-4217 code is carried by the surrounding object or by a
# currency-keyed mapping, and the numeric path names a value/amount/total-like
# quantity.  Keep this structural and currency-agnostic; connector-specific
# fields belong in the connector adapter, not in grounding.
_CURRENCY_CONTEXT_FIELDS = frozenset(
    {
        "currency",
        "currency_code",
        "native_currency",
        "quote_currency",
        "settlement_currency",
        "denomination",
        "unit_currency",
    }
)
_MONEY_PATH_FIELDS = frozenset(
    {
        "amount",
        "balance",
        "cash",
        "cost",
        "coupon",
        "amortization",
        "income",
        "equity",
        "market_value",
        "native",
        "notional",
        "proceeds",
        "total",
        "value",
        "valuation",
    }
)
# Currency declarations that scope a payload's monetary leaves. ``financial_currency``
# is the currency of statement figures, which can differ from the quote currency.
_UNIT_SCOPE_KEYS = _CURRENCY_CONTEXT_FIELDS | frozenset(
    {"financial_currency", "reporting_currency", "statement_currency"}
)

# A leaf's own name says when it is not money even inside a monetary scope:
# ratios, percents and counts sit next to amounts under the same currency.
_RATIO_TOKENS = frozenset(
    (
        "pct percent percentage pp bps ratio ratios rate rates growth margin margins "
        "yield yields weight weights return returns roe roa roic roi beta pe peg corr "
        "correlation vol volatility drawdown probability prob concentration sharpe "
        "sortino alpha hhi to"
    ).split()
)
_COUNT_TOKENS = frozenset(
    (
        "count counts number num n quantity qty units shares opinions analysts "
        "revisions holders positions trades employees obs observations days window "
        "lookback duration rank age volume lots offset limit page returned index id "
        "version year years month months"
    ).split()
)
# A leaf that ends in an amount noun is an amount whatever else its name says.
_AMOUNT_TOKENS = frozenset(
    "amount value price cost balance proceeds total cash pnl".split()
)
# An explicit unit string naming a dimensionless quantity.
_RATIO_UNITS = frozenset("% pct percent percentage ratio fraction x pp bps".split())
_SYMBOL_LIKE_RE = re.compile(r"[A-Z0-9]{1,8}")


def _name_tokens(name: str) -> list[str]:
    """Lower-case words of a camelCase / snake_case field name."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [token for token in re.split(r"[^0-9A-Za-z\u3400-\u9fff]+", spaced.lower()) if token]


def _leaf_dimension(path: str) -> str | None:
    """The dimensionless unit a field name implies, or None.

    A leaf keyed by a symbol or a currency code (``weights.YPFD``) is named by its
    container, so the nearest descriptive ancestor is read instead.

    Args:
        path: Recorded evidence field, e.g. ``"data.sections.financials.revenueGrowth"``.

    Returns:
        ``"ratio"`` for a ratio, percent or metric, ``"count"`` for a count, else None.
    """
    parts = [re.sub(r"\[\d+\]$", "", part) for part in str(path or "").split(".")]
    parts = [part for part in parts if part]
    while (
        len(parts) > 1
        and _SYMBOL_LIKE_RE.fullmatch(parts[-1])
        and not {*_name_tokens(parts[-1])} & (_RATIO_TOKENS | _COUNT_TOKENS | _AMOUNT_TOKENS)
    ):
        parts.pop()
    if not parts:
        return None
    tokens = _name_tokens(parts[-1])
    # A trailing amount noun or currency code (``bond_coupon_ars``) is the leaf's
    # own unit declaration and outweighs any ratio word before it.
    if not tokens or tokens[-1] in _AMOUNT_TOKENS or (
        len(tokens) > 1 and _currency_code(tokens[-1])
    ):
        return None
    if any(token in _RATIO_TOKENS for token in tokens) or _metric_kind_for_path(parts[-1]):
        return "ratio"
    if any(token in _COUNT_TOKENS for token in tokens) or _is_metadata_count_leaf(parts[-1]):
        return "count"
    return None


def _declared_unit(hint: Any) -> str | None:
    """The unit an explicit per-leaf declaration (``_units``) names, or None."""
    if not isinstance(hint, str) or not hint.strip():
        return None
    text = hint.strip()
    if _currency_code(text):
        return "money"
    return "ratio" if text.casefold() in _RATIO_UNITS else "other"


def _declares_currency_scope(payload: Mapping[str, Any]) -> bool:
    """Whether a payload's descriptor objects declare a monetary unit.

    Only the payload root and its first two levels count (``data.listing``), so a
    currency in one deep row does not scope unrelated leaves elsewhere.
    """

    def walk(node: Any, depth: int) -> bool:
        if not isinstance(node, Mapping) or depth > 2:
            return False
        if any(
            str(key).casefold() in _UNIT_SCOPE_KEYS and _currency_code(item)
            for key, item in node.items()
        ):
            return True
        return any(walk(item, depth + 1) for item in node.values())

    return walk(payload, 0)


def _currency_code(value: Any) -> str | None:
    """Return an ISO-shaped currency code carried by generic tool data."""
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    return candidate if is_iso_currency(candidate) else None


def _currency_from_path(path: str) -> str | None:
    """Find a currency code embedded in a generic JSON path."""
    for component in re.split(r"[.\[\]_]+", path):
        currency = _currency_code(component)
        if currency:
            return currency
    return None


def _is_structured_money_field(path: str) -> bool:
    """Whether a path names a value-like leaf, for a record with no unit of its own.

    A dimensionless leaf is never money, and a currency in the path only makes a
    leaf money when the leaf is itself the currency key of an amount mapping.
    """
    components = [
        component.casefold()
        for component in re.split(r"[.\[\]_]+", path)
        if component
    ]
    if not components or _leaf_dimension(path):
        return False
    leaf = components[-1]
    if leaf in _MONEY_PATH_FIELDS or leaf in _AMOUNT_FIELDS:
        return True
    if leaf.endswith("value") or leaf.endswith("amount") or leaf.endswith("total"):
        return True
    if _leaf_name(path) in {"value_start", "value_end"}:
        return True
    return _currency_code(leaf) is not None and any(
        component in _MONEY_PATH_FIELDS for component in components
    )


def _symbol_from_csv_filename(stem: str) -> str | None:
    """Map a run-dir CSV stem (``BYN_V`` -> ``BYN.V``) to a canonical symbol.

    A stem without a known venue suffix (a bare ``AAPL``) maps to None, because
    the project requires an explicit venue suffix.

    Args:
        stem: CSV filename without the ``.csv`` extension.

    Returns:
        The canonical symbol, or None.
    """
    upper = (stem or "").strip().upper()
    if not upper:
        return None
    for raw, canonical in _CSV_FILENAME_SUFFIX_MAP:
        if upper.endswith(raw) and len(upper) > len(raw):
            return upper[: -len(raw)] + canonical
    return None


def _json_object(value: Any) -> dict[str, Any] | None:
    """Parse a JSON object from a tool result when possible."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _is_number(value: Any) -> bool:
    """Return whether a value is a finite JSON-style number, excluding bool."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _coerce_csv_number(value: Any) -> int | float | None:
    """Coerce a CSV text cell (``"0.375"``) to a finite number, or None."""
    if _is_number(value):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value.strip().replace(",", ""))
        except (TypeError, ValueError):
            return None
        if math.isfinite(parsed):
            return parsed
    return None


# "." is not a separator: a decimal price such as 8.5 would parse as a date.
# Only the leading month-day (optional year) matters; annotations after the
# day ("08-10(一)", "08-10盘中") are ignored.
_CLAIM_DATE_RE = re.compile(
    r"^\s*(?:((?:19|20)\d{2})\s*[-/年]\s*)?"
    r"(0?[1-9]|1[0-2])\s*[-/月]\s*([12]\d|3[01]|0?[1-9])"
)


def _claim_date_tuple(date_value: str) -> tuple[int, int] | None:
    """Extract the (month, day) a report-style date cell names.

    Args:
        date_value: Date cell as written in the answer, e.g. ``08-10(周一)``.

    Returns:
        The (month, day) tuple, or None when no date prefix is present.
    """
    match = _CLAIM_DATE_RE.match((date_value or "").strip())
    if match is None:
        return None
    return (int(match.group(2)), int(match.group(3)))


def _timestamp_matches_claim_date(timestamp: str, date_value: str) -> bool:
    """Match an evidence timestamp against the date cell of a claim.

    A year-less cell (``08-05``) matches on month and day. That can match the
    wrong year, so callers still compare the value against every matched record
    rather than trusting the date.

    Args:
        timestamp: Evidence timestamp, normally ISO ``YYYY-MM-DD``.
        date_value: Date cell as written in the answer.

    Returns:
        True when the timestamp denotes the day the claim names.
    """
    stamp = (timestamp or "").strip()
    claim = (date_value or "").strip()
    if not stamp or not claim:
        return False
    if stamp.startswith(claim):
        return True
    claim_tuple = _claim_date_tuple(claim)
    parts = stamp[:10].split("-")
    if claim_tuple is None or len(parts) != 3:
        return False
    try:
        stamp_tuple = (int(parts[1]), int(parts[2]))
    except ValueError:
        return False
    return stamp_tuple == claim_tuple


def _leaf_name(path: str) -> str:
    """The last field name of an evidence path, without its list index."""
    leaf = str(path or "").rsplit(".", 1)[-1]
    return re.sub(r"\[\d+\]$", "", leaf).strip().casefold()


def _price_field_for_path(path: str) -> str | None:
    """Map a generic evidence JSON path to a canonical price field.

    Args:
        path: Recorded evidence field, e.g. ``"data.quote[0].last_price"``.

    Returns:
        The matching member of ``_PRICE_FIELDS``, or ``None`` when the leaf is
        not an unambiguous quote field.
    """
    return _GENERIC_PRICE_FIELD_ALIASES.get(_leaf_name(path))


def _is_metadata_count_leaf(path: str) -> bool:
    """Whether an evidence leaf is a sample size, window or duration.

    Args:
        path: Recorded evidence field, e.g. ``"stats.return_observations"``.

    Returns:
        True for ``n_*`` and ``*_obs`` / ``*_observations`` / ``*_window`` /
        ``*_lookback`` / ``*_count`` / ``*_days`` / ``*_duration`` leaves.
    """
    tokens = [token for token in _leaf_name(path).split("_") if token]
    if not tokens:
        return False
    return tokens[-1] in _METADATA_COUNT_TAILS or (
        len(tokens) > 1 and tokens[0] in _METADATA_COUNT_HEADS
    )


# Price-denominated indicator leaves, registered per tool and matched by path
# prefix (spec §5). An unregistered leaf is still evidence but cannot ground a
# price: inferring price-ness from leaf names admitted ``sma_cross`` as a price.
_REGISTERED_PRICE_INDICATORS: dict[str, tuple[str, ...]] = {
    "technical_indicators": (
        "latest_close",
        "indicators.sma_",
        "indicators.ema_",
        "indicators.bollinger.upper",
        "indicators.bollinger.middle",
        "indicators.bollinger.lower",
    ),
}


def _is_registered_price_indicator(tool: str, path: str) -> bool:
    """Whether a tool registered this leaf as a price-denominated level.

    Args:
        tool: The tool whose result produced the evidence record.
        path: Recorded evidence field, e.g. ``"indicators.bollinger.lower"``.

    Returns:
        True when the tool has a registration whose prefix the path matches.
    """
    prefixes = _REGISTERED_PRICE_INDICATORS.get(str(tool or ""))
    if not prefixes:
        return False
    leaf = str(path or "").strip()
    for prefix in prefixes:
        if leaf == prefix:
            return True
        if not leaf.startswith(prefix):
            continue
        # A prefix ending in "_" names a parametrised family (``sma_20``): only a
        # numeric parameter may follow, so ``sma_cross`` is not a price.
        rest = leaf[len(prefix):]
        if not prefix.endswith("_"):
            return True
        if rest.isdigit():
            return True
    return False


# Tail-risk names short enough to be a fragment of an unrelated leaf count only
# as the whole leaf: "var_explained" and "sales_es" are not a VaR.
_EXACT_ONLY_ALIASES = frozenset({"var", "es"})

# Full-path metric kinds for leaves whose bare name is too generic to alias
# globally. "corr" alone would admit any unrelated leaf named "corr", so
# asistente_casa_portfolio_risk_xray's pairwise correlation reading is matched
# by its exact path suffix instead.
_METRIC_PATH_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("correlation.max_pair.corr", "correlation"),
)


def _metric_kind_for_registered_path(path: str) -> str | None:
    """Path-specific metric kind for a leaf too generic to alias by name alone.

    Args:
        path: Recorded evidence field, e.g. ``"data.correlation.max_pair.corr"``.

    Returns:
        The matching kind, or None when no registered path suffix applies.
    """
    normalized = re.sub(r"\[\d+\]", "", str(path or "")).casefold()
    for suffix, kind in _METRIC_PATH_SUFFIXES:
        if normalized == suffix or normalized.endswith("." + suffix):
            return kind
    return None

# Field-name qualifiers that follow a metric's head and do not change what it
# measures ("hit_rate_daily", "vol_annualized"), like a numeric parameter.
_QUALIFIER_SUFFIXES = frozenset(
    {"daily", "weekly", "monthly", "annual", "annualized", "yearly", "pct", "percent", "bps"}
)


#: Tail-risk measure per field-name token, scanned right to left like every
#: other head-noun rule in this module. VaR and ES/CVaR are different
#: measurements of the same family, and 95% and 99% are different numbers of
#: either, so the family alone cannot say which value a figure quotes (#1425).
_TAIL_RISK_MEASURE_TOKENS = {
    "var": "var",
    "cvar": "es",
    "es": "es",
    "shortfall": "es",
}


def tail_risk_identity(path: str) -> str | None:
    """The tail-risk identity an evidence field names, e.g. ``var_95``.

    Read off the FIELD NAME a tool returned — never off answer prose, which the
    gate does not interpret. ``data.tail_risk.var_95`` and ``historical_var``
    are ``var_95`` and ``var``; ``cvar_99`` and ``es_99`` are both ``es_99``,
    which is the point: they are the same measurement under two names.

    Args:
        path: Evidence JSON path or leaf name.

    Returns:
        ``"<measure>"`` or ``"<measure>_<confidence>"``, or None when the field
        is not a tail-risk value at all.
    """
    if _metric_kind_for_path(path) != "tail_risk":
        return None
    tokens = [token for token in re.split(r"[_.]", _leaf_name(path)) if token]
    confidence = tokens[-1] if tokens and tokens[-1].isdigit() else ""
    measure = None
    for token in reversed([token for token in tokens if not token.isdigit()]):
        measure = _TAIL_RISK_MEASURE_TOKENS.get(token)
        if measure is not None:
            break
    if measure is None:
        return None
    return f"{measure}_{confidence}" if confidence else measure


def _metric_kind_for_path(path: str) -> str | None:
    """Map an evidence JSON path to an analysis metric kind.

    A metadata count leaf (:func:`_is_metadata_count_leaf`) has no kind.
    """
    if _is_metadata_count_leaf(path):
        return None
    registered = _metric_kind_for_registered_path(path)
    if registered is not None:
        return registered
    leaf = _leaf_name(path)
    kind = _ANALYSIS_KIND_ALIASES.get(leaf)
    if kind is not None:
        return kind
    # Compound leaves ("strategy_max_drawdown"): scan tokens from the right,
    # where English puts the head noun, so "return_vol" is vol, not return.
    # Only the head of a compound leaf says what it measures (#1426):
    # "strategy_max_drawdown" is a drawdown, while "sharpe_sample_size" and
    # "drawdown_threshold" are a size and a threshold. A trailing numeric
    # parameter or period qualifies the head ("cvar_95", "hit_rate_daily").
    tokens = [token for token in re.split(r"[_.]", leaf) if token]
    stripped = list(tokens)
    while stripped and (stripped[-1].isdigit() or stripped[-1] in _QUALIFIER_SUFFIXES):
        stripped.pop()
    for candidate in (tokens, stripped):
        for size in (2, 1):
            if len(candidate) < size:
                continue
            key = "_".join(candidate[-size:])
            kind = None if key in _EXACT_ONLY_ALIASES else _ANALYSIS_KIND_ALIASES.get(key)
            if kind is not None:
                return kind
    return None


@dataclass(frozen=True)
class EvidenceRecord:
    """One observed, unavailable, or derived numeric evidence item.

    ``artifact`` and ``scope`` are set only for backtest output: the file the
    value was read from and the backtest run directory that produced it, both
    relative to the ledger's run directory ("" is that directory itself). A
    declaration's ``ref`` may name either one.

    ``unit`` is what the evidence says the value is: ``money`` (an explicit
    currency unit, or a declared currency scope), ``ratio``, ``count`` or
    ``other``. None means the payload said nothing, so name rules apply.
    """

    call_id: str
    tool: str
    symbol: str | None
    source: str
    timestamp: str | None
    field: str
    value: int | float | None
    status: str
    currency: str | None = None
    venue: str | None = None
    currency_conversion: str | None = None
    artifact: str | None = None
    scope: str | None = None
    unit: str | None = None
    # Generic structured evidence distinguishes a bound entity from an
    # aggregate, an unidentified item, and contradictory identities.
    identity_scope: str | None = None


@dataclass(frozen=True)
class _EntityContext:
    symbol: str | None
    scope: str  # entity, aggregate, unknown, or conflict
    origin: str = "inferred"


_ITEM_IDENTITY_FIELDS = frozenset({"symbol", "ticker", "entity_id"})


def _item_symbol(value: Mapping[str, Any], allowed: set[str]) -> tuple[str | None, bool]:
    """Read explicit per-item identities, excluding unrelated status codes."""
    symbols: set[str] = set()
    for key, item in value.items():
        if not isinstance(item, str) or not item.strip():
            continue
        name = str(key).casefold()
        normalized = _normalize_symbol(item)
        if name in _ITEM_IDENTITY_FIELDS or (
            name == "code" and (normalized in allowed or _CANONICAL_SYMBOL_RE.fullmatch(item))
        ):
            symbols.add(normalized)
    return (next(iter(symbols)), False) if len(symbols) == 1 else (None, len(symbols) > 1)


def _entity_child(
    parent: _EntityContext, explicit: str | None, allowed: set[str]
) -> _EntityContext:
    if explicit is None:
        return parent
    if parent.scope == "conflict" or (parent.scope == "entity" and parent.symbol != explicit):
        return _EntityContext(explicit, "conflict")
    if allowed and explicit not in allowed:
        return _EntityContext(explicit, "conflict")
    return _EntityContext(explicit, "entity", "explicit")


def _entity_for_key(
    parent: _EntityContext, key: Any, allowed: set[str]
) -> _EntityContext:
    label = str(key)
    if _CANONICAL_SYMBOL_RE.fullmatch(label):
        return _entity_child(parent, _normalize_symbol(label), allowed)
    if label.casefold() in {"aggregate", "totals"} and parent.origin != "explicit":
        return _EntityContext(None, "aggregate", "explicit")
    return parent


def _list_entity_context(items: list[Any], parent: _EntityContext) -> _EntityContext:
    if len(items) < 2 or parent.origin == "explicit" or parent.scope == "conflict":
        return parent
    # Repeated dated observations can inherit one call's entity; otherwise an
    # anonymous collection could contain several different entities.
    if all(
        isinstance(item, dict)
        and any(item.get(key) is not None for key in _TIMESTAMP_FIELDS)
        for item in items
    ):
        return parent
    return _EntityContext(None, "unknown")


def _record_matches_entity(record: EvidenceRecord, symbol: str | None) -> bool:
    """Match entity-scoped claims without blocking legitimate unscoped evidence."""
    if record.identity_scope == "conflict":
        return False
    if symbol is None:
        return True
    if record.identity_scope in {"unknown", "aggregate"}:
        return False
    if record.identity_scope == "entity":
        return record.symbol == symbol
    # Legacy non-generic sources retain their established semantics.
    return not record.symbol or record.symbol == symbol


def _is_price_kind(record: EvidenceRecord) -> bool:
    """Whether a record is money-denominated: a quote, a registered level or an amount.

    Args:
        record: One evidence record, with its raw or canonical field.

    Returns:
        True for OHLC/quote fields, registered price indicators and
        amount/turnover leaves.
    """
    return (
        record.field in _PRICE_FIELDS
        or _price_field_for_path(record.field) is not None
        or _is_registered_price_indicator(record.tool, record.field)
        or _leaf_name(record.field) in _AMOUNT_FIELDS
        or record.unit == "money"
        or (
            record.unit is None
            and record.currency is not None
            and _is_structured_money_field(record.field)
        )
    )


class _EvidenceMixin:
    """Evidence behaviour of :class:`GroundingLedger`."""

    def _numeric_entity_context(
        self, arguments: Mapping[str, Any]
    ) -> tuple[_EntityContext, set[str]]:
        symbols = {
            self._match_authorized_symbol(symbol, self.authorized_symbols) or symbol
            for symbol in self._extract_symbol_arguments(arguments)
        }
        if len(symbols) == 1:
            return _EntityContext(next(iter(symbols)), "entity", "argument"), symbols
        if symbols:
            return _EntityContext(None, "unknown"), symbols
        # A later scalar analysis call may omit a symbol argument after this
        # session has observed exactly one entity. Require that the session has
        # no other candidate; an explicit aggregate subtree still stays global.
        observed = {
            record.symbol
            for record in self._evidence
            if record.status == "observed" and record.symbol and record.identity_scope != "conflict"
        }
        if len(observed) == 1:
            if self._session_symbols <= observed:
                return _EntityContext(next(iter(observed)), "entity", "observed"), symbols
            return _EntityContext(None, "unknown"), symbols
        if len(observed) > 1:
            return _EntityContext(None, "unknown"), symbols
        # A symbol mentioned in the user's prose is not evidence that a
        # symbol-less tool result belongs to that entity. With no observed or
        # argument identity, keep the result unscoped/aggregate.
        return _EntityContext(None, "aggregate"), symbols

    def _ingest_analysis_result(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        payload: dict[str, Any] | None,
        call_id: str,
    ) -> None:
        """Record metric figures a completed analysis result actually produced.

        An ok envelope is not enough: ``backtest`` reports ok for any runner exit
        and a skipped call carries no result (#1336). Only a result yielding at
        least one recognisable metric counts as completed analysis.
        """
        if payload is None or payload.get("skipped"):
            return
        if tool_name == "backtest":
            if str(payload.get("status") or "").casefold() != "ok" and payload.get(
                "exit_code"
            ) not in (0, "0"):
                return
            recorded = self._record_backtest_metrics(arguments, payload, call_id)
        elif tool_name == "factor_analysis":
            if str(payload.get("status") or "").casefold() != "ok":
                return
            recorded = self._record_leaf_metrics(payload, call_id, tool_name, "", arguments)
        elif tool_name == "run_shadow_backtest":
            if str(payload.get("status") or "").casefold() != "ok":
                return
            combined = payload.get("combined")
            if not isinstance(combined, dict):
                # A combined dict containing only {"error": ...} is no analysis.
                return
            recorded = self._record_leaf_metrics(combined, call_id, tool_name, "combined", arguments)
        elif tool_name == "quantlib_call":
            if payload.get("ok") is not True or str(
                arguments.get("action") or ""
            ).casefold() != "call":
                return
            function = str(arguments.get("function") or "")
            recorded = self._record_leaf_metrics(
                payload.get("result"), call_id, tool_name, function, arguments
            )
        else:
            return
        if recorded:
            self._analysis_completed.append(
                {"call_id": call_id, "tool": tool_name, "recorded_at": _utc_now()}
            )

    def _record_leaf_metrics(
        self,
        value: Any,
        call_id: str,
        tool_name: str,
        field_prefix: str,
        arguments: Mapping[str, Any],
    ) -> int:
        """Record metric leaves with the identity of their containing item."""
        recorded = 0
        root, allowed = self._numeric_entity_context(arguments)

        def visit(item: Any, path: str, context: _EntityContext) -> None:
            nonlocal recorded
            if _is_number(item):
                kind = _metric_kind_for_path(path)
                if kind is None:
                    return
                self._analysis_metrics.append(
                    {
                        "metric": kind,
                        "value": float(item),
                        "tool": tool_name,
                        "call_id": call_id,
                        "field": path,
                        "symbol": context.symbol,
                        "identity_scope": context.scope,
                    }
                )
                recorded += 1
                return
            if isinstance(item, dict):
                explicit, contradictory = _item_symbol(item, allowed)
                local = _entity_child(context, explicit, allowed)
                if contradictory:
                    local = _EntityContext(None, "conflict")
                for key, child in item.items():
                    visit(
                        child,
                        f"{path}.{key}" if path else str(key),
                        _entity_for_key(local, key, allowed),
                    )
            elif isinstance(item, list):
                for index, child in enumerate(item):
                    visit(child, f"{path}[{index}]", _list_entity_context(item, context))

        visit(value, field_prefix or "", root)
        return recorded

    def _record_backtest_metrics(
        self,
        arguments: Mapping[str, Any],
        payload: dict[str, Any],
        call_id: str,
    ) -> int:
        """Parse metric figures from a successful backtest's run-dir artifacts."""
        root = self.run_dir.resolve()
        candidates: list[Path] = []
        own_dir: Path | None = None
        raw_dir = arguments.get("run_dir") or payload.get("run_dir")
        if raw_dir:
            candidate = Path(str(raw_dir))
            if not candidate.is_absolute():
                candidate = self.run_dir / candidate
            try:
                resolved = candidate.resolve()
                if resolved == root or resolved.is_relative_to(root):
                    candidates.append(resolved)
                    own_dir = resolved
            except OSError:
                pass
        # The loop archives a detached backtest's artifacts into the active run
        # dir right after it succeeds, so that copy is the second candidate.
        candidates.append(root)
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, dict):
            for path_value in artifacts.values():
                if not isinstance(path_value, str):
                    continue
                try:
                    resolved = Path(path_value).resolve()
                    if resolved.is_relative_to(root):
                        candidates.append(resolved)
                except OSError:
                    continue
        files: list[Path] = []
        seen_dirs: set[Path] = set()
        for candidate in candidates:
            if candidate.is_file():
                files.append(candidate)
                continue
            if candidate in seen_dirs:
                continue
            seen_dirs.add(candidate)
            for dir_path in (candidate, candidate / "artifacts"):
                for name in ("metrics.csv", "metrics.json"):
                    target = dir_path / name
                    if target.is_file():
                        files.append(target)
        recorded = 0
        seen_files: set[Path] = set()
        for file_path in files:
            if file_path in seen_files:
                continue
            seen_files.add(file_path)
            recorded += self._record_metrics_file(file_path, call_id)
        if recorded:
            own_dir = own_dir or root
            scope = _relative_posix(own_dir, root)
            self._backtest_scopes[call_id] = scope
            self._scope_latest[scope] = call_id
            self._record_backtest_outputs(own_dir, call_id, scope)
            # The active run holds this backtest's copy only when the loop's
            # archive names it as the source; otherwise it is an earlier run's.
            if own_dir != root and _archive_source(root) == own_dir.name:
                self._record_backtest_outputs(root, call_id, scope)
        return recorded

    def _record_backtest_outputs(self, directory: Path, call_id: str, scope: str) -> None:
        """Record every scalar of a backtest's summary outputs, and hash its tables.

        The run's Sortino, turnover, final equity, Monte Carlo p-values and
        risk X-ray are as observed as its Sharpe; only kind-named metrics used
        to count, so a report quoting the rest was refused figure by figure.
        A file re-recorded under the same path replaces its earlier records:
        the loop's archive overwrites the active run's copy on every backtest.

        Args:
            directory: A backtest run directory, or the active run holding its copy.
            call_id: The backtest call that produced the outputs.
            scope: That backtest's run directory relative to the ledger's.
        """
        root = self.run_dir.resolve()
        manifest = _run_card_manifest(directory)
        room = _MAX_GENERIC_EVIDENCE
        for name in _BACKTEST_SUMMARY_FILES:
            path = directory / name
            if not path.is_file() or self._is_model_written(path):
                continue
            if name not in _MANIFEST_EXEMPT and manifest.get(name) != _file_sha256(path):
                continue
            artifact = _relative_posix(path, root)
            self._evidence = [record for record in self._evidence if record.artifact != artifact]
            for field_name, value in _summary_scalars(path):
                if room <= 0:
                    return
                if _price_field_for_path(field_name) is not None:
                    continue
                self._evidence.append(
                    EvidenceRecord(
                        call_id=call_id,
                        tool="backtest",
                        symbol=None,
                        source="backtest",
                        timestamp=None,
                        field=field_name,
                        value=value,
                        status="observed",
                        artifact=artifact,
                        scope=scope,
                    )
                )
                room -= 1
        tables = directory / "artifacts"
        if tables.is_dir():
            for path in sorted(tables.glob("*.csv")):
                if self._is_model_written(path) or f"artifacts/{path.name}" in _BACKTEST_SUMMARY_FILES:
                    continue
                digest = _file_sha256(path)
                if digest is not None:
                    self._engine_tables[str(path.resolve())] = (digest, scope)

    def _note_model_write(
        self, tool_name: str, arguments: Mapping[str, Any], payload: dict[str, Any] | None
    ) -> None:
        """Remember a file the model wrote, so it never passes for engine output."""
        if tool_name not in _WRITE_TOOLS:
            return
        raw = (payload or {}).get("path") or arguments.get("path") or arguments.get("file_path")
        if not raw:
            return
        path = Path(str(raw))
        if not path.is_absolute():
            path = Path(str(arguments.get("run_dir") or self.run_dir)) / path
        try:
            self._model_written.add(str(path.resolve()))
        except OSError:
            return

    def _is_model_written(self, path: Path) -> bool:
        """Whether the model wrote ``path`` itself in this run."""
        try:
            return str(path.resolve()) in self._model_written
        except OSError:
            return True

    def _ingest_engine_table(self, payload: dict[str, Any], call_id: str) -> None:
        """Record the rows of a backtest table the model just read back.

        Only a table a completed backtest wrote counts, and only while it is
        still byte for byte what the engine wrote. Only the rows the model was
        shown are recorded: a per-bar table is a dense series, and recording
        all of it would let an invented weight or return match some bar by
        chance. Price columns are left out, as in a summary file.

        Args:
            payload: The ``read_file`` result.
            call_id: The ``read_file`` call.
        """
        raw, content = payload.get("path"), payload.get("content")
        if not isinstance(raw, str) or not isinstance(content, str):
            return
        try:
            path = Path(raw).resolve()
        except OSError:
            return
        known = self._engine_tables.get(str(path))
        if known is None or _file_sha256(path) != known[0]:
            return
        scope = known[1]
        truncated = "\n... (truncated)"
        if content.endswith(truncated):
            content = content[: -len(truncated)]
            content = content[: content.rfind("\n") + 1]
        try:
            rows = list(csv.reader(content.splitlines()))
        except csv.Error:
            return
        if len(rows) < 2:
            return
        header = [cell.strip() for cell in rows[0]]
        folded = [cell.casefold() for cell in header]
        date_index = next(
            (index for index, name in enumerate(folded) if name in _CSV_DATE_COLUMNS), None
        )
        symbol_index = next(
            (index for index, name in enumerate(folded) if name in _TABLE_SYMBOL_COLUMNS), None
        )
        artifact = _relative_posix(path, self.run_dir.resolve())
        room = _MAX_GENERIC_EVIDENCE
        for row in rows[1:]:
            timestamp = (
                row[date_index].strip()
                if date_index is not None and date_index < len(row)
                else None
            )
            for index, cell in enumerate(row[: len(header)]):
                if index in (date_index, symbol_index):
                    continue
                value = _coerce_csv_number(cell)
                if value is None:
                    continue
                # A weight column is named by its instrument ("000001.SZ"), and
                # that name is the field a ref narrows on. The record carries no
                # symbol: a portfolio table is not evidence about the listing,
                # and a symbol here would start attributing the answer's
                # portfolio figures to one instrument.
                column_symbol = _cell_symbol(header[index])
                if column_symbol is None and _price_field_for_path(folded[index]) is not None:
                    continue
                if room <= 0:
                    return
                self._evidence.append(
                    EvidenceRecord(
                        call_id=call_id,
                        tool="read_file",
                        symbol=None,
                        source="backtest",
                        timestamp=timestamp,
                        field=column_symbol or folded[index],
                        value=value,
                        status="observed",
                        artifact=artifact,
                        scope=scope,
                    )
                )
                room -= 1

    def _record_metrics_file(self, path: Path, call_id: str) -> int:
        """Record metric figures from one metrics.csv/metrics.json artifact."""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return 0
        if path.suffix == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return 0
            if not isinstance(data, dict):
                return 0
            return self._record_leaf_metrics(data, call_id, "backtest", "")
        try:
            rows = list(csv.reader(text.splitlines()))
        except csv.Error:
            return 0
        if len(rows) < 2:
            return 0
        header = [cell.strip().casefold() for cell in rows[0]]
        recorded = 0
        for index, raw in enumerate(rows[1]):
            if index >= len(header):
                break
            kind = _ANALYSIS_KIND_ALIASES.get(header[index])
            value = _coerce_csv_number(raw)
            if kind is not None and value is not None:
                self._analysis_metrics.append(
                    {
                        "metric": kind,
                        "value": float(value),
                        "tool": "backtest",
                        "call_id": call_id,
                        "field": header[index],
                    }
                )
                recorded += 1
        return recorded

    def _record_tool_failure(self, tool_name: str, call_id: str, result: str) -> None:
        """Store structured unavailable evidence for failed business envelopes."""
        payload = _json_object(result) or {}
        self._tool_failures.append(
            {
                "call_id": call_id,
                "tool": tool_name,
                "status": "unavailable",
                "error_code": payload.get("error_code"),
                "message": str(payload.get("error") or payload.get("message") or "tool failed")[:500],
                "recorded_at": _utc_now(),
            }
        )

    def _ingest_market_data(
        self,
        arguments: Mapping[str, Any],
        payload: dict[str, Any] | None,
        call_id: str,
    ) -> None:
        """Convert full OHLCV payloads into source-linked evidence rows."""
        if payload is None:
            self._record_tool_failure("get_market_data", call_id, "malformed JSON result")
            return
        requested_source = str(arguments.get("source") or "auto")
        provenance = payload.get("_provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        for raw_symbol, raw_rows in payload.items():
            if str(raw_symbol).startswith("_"):
                continue
            symbol = _normalize_symbol(raw_symbol)
            rows = raw_rows.get("data") if isinstance(raw_rows, dict) else raw_rows
            if not isinstance(rows, list):
                continue
            symbol_provenance = provenance.get(raw_symbol)
            actual_source = (
                str(symbol_provenance.get("source"))
                if isinstance(symbol_provenance, dict) and symbol_provenance.get("source")
                else requested_source
            )
            currency_conversion = (
                str(symbol_provenance.get("currency_conversion"))
                if isinstance(symbol_provenance, dict)
                and symbol_provenance.get("currency_conversion")
                else None
            )
            # Preserve source-declared listing currency when one venue lists
            # instruments in more than one currency; identity suffixes alone
            # do not establish the quote currency for every line.
            quote_currency = (
                str(symbol_provenance.get("quote_currency"))
                if isinstance(symbol_provenance, dict)
                and symbol_provenance.get("quote_currency")
                else _infer_currency(symbol)
            )
            for row in rows:
                if not isinstance(row, dict):
                    continue
                timestamp = next(
                    (str(row[key]) for key in _TIMESTAMP_FIELDS if row.get(key) is not None),
                    None,
                )
                for field_name, value in row.items():
                    normalized_field = str(field_name).casefold()
                    if normalized_field in _TIMESTAMP_FIELDS or not _is_number(value):
                        continue
                    self._evidence.append(
                        EvidenceRecord(
                            call_id=call_id,
                            tool="get_market_data",
                            symbol=symbol,
                            source=actual_source,
                            timestamp=timestamp,
                            field=normalized_field,
                            value=value,
                            status="observed",
                            currency=quote_currency,
                            venue=_infer_venue(symbol),
                            currency_conversion=currency_conversion,
                        )
                    )
        unresolved = payload.get("_unresolved")
        if isinstance(unresolved, list):
            for raw_symbol in unresolved:
                symbol = _normalize_symbol(raw_symbol)
                self._evidence.append(
                    EvidenceRecord(
                        call_id=call_id,
                        tool="get_market_data",
                        symbol=symbol,
                        source=requested_source,
                        timestamp=None,
                        field="availability",
                        value=None,
                        status="unavailable",
                        currency=_infer_currency(symbol),
                        venue=_infer_venue(symbol),
                    )
                )

    def _ingest_generic_numeric(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        payload: dict[str, Any],
        call_id: str,
    ) -> None:
        """Flatten bounded numeric leaves from other market-sensitive tools."""
        root, allowed = self._numeric_entity_context(arguments)
        source = str(payload.get("source") or tool_name)
        remaining = _MAX_GENERIC_EVIDENCE
        timestamp_fields = (*_TIMESTAMP_FIELDS, "latest_date", "as_of")
        payload_scope = _declares_currency_scope(payload)

        def visit(
            value: Any,
            path: str,
            timestamp: str | None = None,
            currency: str | None = None,
            unit_hint: Any = None,
            context: _EntityContext = root,
        ) -> None:
            nonlocal remaining
            if remaining <= 0:
                return
            if _is_number(value):
                evidence_currency = (
                    currency
                    or _currency_from_path(path)
                    or _infer_currency(context.symbol or "")
                )
                # The unit is what the payload declares, never what the venue of
                # the symbol implies: an explicit per-leaf unit, else a declared
                # currency scope for a leaf that is not a ratio or a count.
                unit = _declared_unit(unit_hint) or _leaf_dimension(path)
                if unit is None and (currency or payload_scope):
                    unit = "money"
                self._evidence.append(
                    EvidenceRecord(
                        call_id=call_id,
                        tool=tool_name,
                        symbol=context.symbol,
                        source=source,
                        timestamp=timestamp,
                        field=path or "value",
                        value=value,
                        status="observed",
                        currency=evidence_currency,
                        venue=_infer_venue(context.symbol or ""),
                        identity_scope=context.scope,
                        unit=unit,
                    )
                )
                remaining -= 1
                return
            if isinstance(value, dict):
                explicit, contradictory = _item_symbol(value, allowed)
                local = _entity_child(context, explicit, allowed)
                if contradictory:
                    local = _EntityContext(None, "conflict")
                local_timestamp = next(
                    (
                        str(value[key])
                        for key in timestamp_fields
                        if value.get(key) is not None
                    ),
                    timestamp,
                )
                local_currency = currency or _currency_from_path(path)
                for key, item in value.items():
                    if str(key).casefold() in _CURRENCY_CONTEXT_FIELDS:
                        local_currency = _currency_code(item) or local_currency
                units = value.get("_units")
                units = units if isinstance(units, dict) else {}
                for key, item in value.items():
                    if str(key).casefold() in timestamp_fields:
                        continue
                    child_path = f"{path}.{key}" if path else str(key)
                    visit(
                        item,
                        child_path,
                        local_timestamp,
                        local_currency or _currency_from_path(child_path),
                        units.get(key),
                        _entity_for_key(local, key, allowed),
                    )
            elif isinstance(value, list):
                item_context = _list_entity_context(value, context)
                for index, item in enumerate(value):
                    visit(item, f"{path}[{index}]", timestamp, currency, None, item_context)

        visit(payload, "")

    def _ingest_run_dir_ohlc_csvs(self) -> None:
        """Register OHLC rows from per-symbol CSVs the run wrote via bash+yfinance.

        Those prices were observed but bypassed ``get_market_data``. Only a file
        whose name maps to a symbol already tracked in this run is accepted, so a
        stray CSV cannot mint identity; rows are bounded by
        ``_MAX_GENERIC_EVIDENCE`` and each file is ingested once.
        """
        if not self.run_dir.is_dir():
            return
        entitled = self._session_symbols | self.authorized_symbols
        if not entitled:
            return
        room = _MAX_GENERIC_EVIDENCE
        for path in sorted(self.run_dir.rglob("*.csv")):
            if room <= 0:
                return
            try:
                identity_key = f"{path.resolve()}:{path.stat().st_mtime_ns}"
            except (OSError, ValueError):
                continue
            if identity_key in self._ingested_csvs:
                continue
            self._ingested_csvs.add(identity_key)
            symbol = _symbol_from_csv_filename(path.stem)
            if not symbol or symbol not in entitled:
                continue
            try:
                with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                    rows = list(csv.DictReader(handle))
            except (OSError, UnicodeDecodeError, csv.Error):
                continue
            for row in rows:
                if room <= 0:
                    return
                if not isinstance(row, dict):
                    continue
                timestamp = next(
                    (
                        str(row[key]).strip()
                        for key in row
                        if str(key).strip().casefold() in _CSV_DATE_COLUMNS
                        and row[key] not in (None, "")
                    ),
                    None,
                )
                for key, value in row.items():
                    field_name = _CSV_PRICE_COLUMNS.get(
                        str(key).strip().casefold().replace(" ", "_")
                    )
                    if field_name is None:
                        continue
                    numeric = _coerce_csv_number(value)
                    if numeric is None:
                        continue
                    self._evidence.append(
                        EvidenceRecord(
                            call_id=f"csv:{path.name}",
                            tool="bash",
                            symbol=symbol,
                            source="yfinance",
                            timestamp=timestamp,
                            field=field_name,
                            value=numeric,
                            status="observed",
                            currency=_infer_currency(symbol),
                            venue=_infer_venue(symbol),
                        )
                    )
                    room -= 1

    def _price_records(self) -> list[EvidenceRecord]:
        """Return observed OHLC/price evidence only."""
        return [
            record
            for record in self._evidence
            if record.status == "observed"
            and record.field in _PRICE_FIELDS
            and record.value is not None
        ]

    def _comparable_price_records(self) -> list[EvidenceRecord]:
        """Return every observed quote a numeric claim may be checked against.

        Quotes from non-OHLC tools are re-keyed onto canonical price fields and
        registered indicator leaves onto ``indicator``.

        Returns:
            Observed price evidence with canonical ``field`` values.
        """
        records = self._price_records()
        already_counted = {id(record) for record in records}
        for record in self._evidence:
            if id(record) in already_counted:
                continue
            if record.status != "observed" or record.value is None:
                continue
            field_name = _price_field_for_path(record.field)
            if field_name is None:
                if _is_registered_price_indicator(record.tool, record.field):
                    records.append(replace(record, field="indicator"))
                continue
            records.append(replace(record, field=field_name))
        return records
