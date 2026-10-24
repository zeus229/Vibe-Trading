"""Price-basis validation for BYMA bars from the existing Yahoo/yfinance loaders.

Both loaders deliver split- and dividend-adjusted OHLC with raw volume (the
same caliber as every other market). Audited on 2026-10-24, that holds for
BYMA shares, but Yahoo's CEDEAR series do NOT absorb every conversion-ratio
change: AAPL.BA halves on 2024-01-24 (ratio 10 -> 20) and MSFT/GOOGL/AMZN/
NVDA.BA halve on 2020-10-22, while the investor's holding doubled. Left as
is, a backtest books those unit changes as 50-75% losses.

This module therefore checks every declared ratio change against the price
jump the data actually shows and, only when the evidence is unambiguous,
rescales the earlier bars exactly as a split adjustment would. Anything it
cannot prove fails closed. It is not a corporate-action engine: dividends
stay in the source's adjusted prices and no cashflow is synthesized.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

PRICE_COLUMNS = ("open", "high", "low", "close")
PRICE_SERIES_DECLARATIONS = (None, "adjusted", "unadjusted")
# A one-bar move this large in a CEDEAR, with no declared ratio change, is
# treated as an unexplained unit discontinuity rather than market movement.
DEFAULT_MAX_UNEXPLAINED_CEDEAR_MOVE = 0.4
# Shares get a wider band: real BYMA sessions have moved ~47% (2019-08-12),
# while an unadjusted 4:1 or larger split would move 75% or more.
DEFAULT_MAX_UNEXPLAINED_SHARE_MOVE = 0.75
# How far an observed jump may sit from the expected factor (market noise).
DEFAULT_RATIO_JUMP_TOLERANCE = 0.2


def _traded(frame: pd.DataFrame) -> pd.DataFrame:
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    return frame[volume > 0]


def _near(observed: float, target: float, tolerance: float) -> bool:
    return abs(math.log(observed / target)) <= math.log1p(tolerance)


def drop_non_session_bars(
    frame: pd.DataFrame, symbol: str, sessions: Sequence[date],
) -> Tuple[pd.DataFrame, int]:
    """Drop source placeholder bars on non-sessions; fail on traded ones.

    Yahoo emits flat, zero-volume bars on BYMA holidays (e.g. 2018-01-01).
    A non-session bar that reports volume means the calendar and the data
    disagree, which no adjustment can resolve.
    """
    session_set = set(sessions)
    off = [ts for ts in frame.index if ts.date() not in session_set]
    if not off:
        return frame, 0
    volume = pd.to_numeric(frame.loc[off, "volume"], errors="coerce")
    traded = [str(ts.date()) for ts, v in volume.items() if not (v <= 0)]
    if traded:
        raise ValueError(
            f"{symbol}: bars with volume on dates outside the settlement "
            f"calendar ({', '.join(traded[:5])}); calendar and data disagree"
        )
    return frame.drop(index=off), len(off)


def reconcile_ratio_changes(
    frame: pd.DataFrame,
    symbol: str,
    ratio_history: Iterable[Dict[str, Any]],
    *,
    tolerance: float = DEFAULT_RATIO_JUMP_TOLERANCE,
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """Bring every in-window ratio change onto one adjusted unit basis.

    For a change from ratio ``old`` to ``new`` (CEDEARs per underlying share)
    an unadjusted series moves by ``old/new`` across the effective date and
    an adjusted one by ~1. The observed close-to-close move between the last
    traded bar before and the first traded bar on/after the date decides
    which, optionally checked against the entry's declared ``price_series``.
    Unadjusted evidence rescales earlier OHLC by ``old/new`` (volume by the
    inverse) so held quantities keep their value through the change.
    """
    entries = list(ratio_history)
    events: List[Dict[str, Any]] = []
    if frame.empty or len(entries) < 2:
        return frame, events
    first, last = frame.index[0].date(), frame.index[-1].date()
    adjusted = frame.copy()
    traded = _traded(frame)
    for previous, entry in zip(entries, entries[1:]):
        effective = date.fromisoformat(str(entry["effective_date"]))
        if not first < effective <= last:
            continue
        old, new = float(previous["ratio"]), float(entry["ratio"])
        factor = old / new
        declared = entry.get("price_series")
        before = traded[traded.index.date < effective]
        after = traded[traded.index.date >= effective]
        if before.empty or after.empty:
            raise ValueError(
                f"{symbol}: ratio change on {effective} cannot be verified; "
                "no traded bar on both sides of it"
            )
        observed = float(after["close"].iloc[0]) / float(before["close"].iloc[-1])
        looks_unadjusted = _near(observed, factor, tolerance)
        looks_adjusted = _near(observed, 1.0, tolerance)
        if declared == "unadjusted":
            verdict = looks_unadjusted and not looks_adjusted
        elif declared == "adjusted":
            verdict = looks_adjusted and not looks_unadjusted
        else:
            verdict = looks_unadjusted != looks_adjusted
        if not verdict:
            raise ValueError(
                f"{symbol}: ratio change {old:g}->{new:g} on {effective} is not "
                f"provable from prices (observed move x{observed:.3f}, "
                f"unadjusted would be x{factor:.3f}, adjusted ~x1; declared "
                f"{declared or 'nothing'}). Supply consistent data or a "
                "price_series declaration the bars support"
            )
        basis = "unadjusted" if looks_unadjusted else "adjusted"
        if basis == "unadjusted":
            earlier = adjusted.index.date < effective
            for column in PRICE_COLUMNS:
                adjusted.loc[earlier, column] = adjusted.loc[earlier, column] * factor
            adjusted.loc[earlier, "volume"] = adjusted.loc[earlier, "volume"] / factor
        events.append({
            "symbol": symbol,
            "effective_date": str(effective),
            "old_ratio": old,
            "new_ratio": new,
            "observed_move": round(observed, 6),
            "source_basis": basis,
            "rescaled_prior_bars": basis == "unadjusted",
        })
    return adjusted, events


def unexplained_discontinuities(
    frame: pd.DataFrame, max_move: float = DEFAULT_MAX_UNEXPLAINED_CEDEAR_MOVE,
) -> List[str]:
    """Dates where consecutive traded closes move by more than ``max_move``."""
    closes = _traded(frame)["close"].astype(float)
    if len(closes) < 2:
        return []
    moves = closes / closes.shift(1)
    limit = math.log(1.0 / (1.0 - max_move))
    return [str(ts.date()) for ts, move in moves.iloc[1:].items()
            if abs(math.log(move)) > limit]


def validate_frame_contract(
    frame: pd.DataFrame, symbol: str, currency: str,
) -> None:
    """Require OHLCV columns and a source-declared quote currency match."""
    missing = [c for c in (*PRICE_COLUMNS, "volume") if c not in frame.columns]
    if missing:
        raise ValueError(f"{symbol}: BYMA bars require columns {missing}")
    declared: Optional[str] = frame.attrs.get("quote_currency")
    if declared != currency:
        raise ValueError(
            f"{symbol}: instrument metadata says {currency} but the source "
            f"declared {declared or 'no quote currency'}; BYMA bars must carry "
            "the loader's declared currency"
        )
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.has_duplicates:
        raise ValueError(f"{symbol}: BYMA bars need a unique DatetimeIndex")
    if not frame.index.is_monotonic_increasing:
        raise ValueError(f"{symbol}: BYMA bars must be in ascending order")
