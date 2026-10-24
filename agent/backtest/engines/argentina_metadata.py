"""Automatic, provenance-labelled BYMA instrument preparation.

Each value the BYMA engine needs is either declared by the caller (always
wins) or resolved here from an existing source, and every resolved value is
labelled with its kind of evidence:

- ``official``: an exchange document (BYMA Circular 3569 tick schedule).
- ``derived``: computed from data the run already loaded (sessions from
  traded bars; ARS from the ``.BA`` pool contract checked against the
  loader's declared currency).
- ``third_party``: a data vendor's descriptor (Yahoo's ``shortName``).
- ``assumption``: a simulation convention with no official number behind it
  (one share per order lot), reported so it is never mistaken for a rule.

Anything essential that cannot be resolved this way fails closed with a
message naming the exact configuration keys to declare.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Callable, Dict, Iterable, Optional, Tuple

import pandas as pd

# BYMA Circular N° 3569 "Reglas de Negocio en el Segmento PPT Operaciones de
# Contado", Anexo, version 04-07-2024, "Alteración mínima de las órdenes,
# Renta Variable" (explicitly including CEDEARs and ADR-like receipts).
# Source: https://data-widgets.byma.com.ar/wp-content/uploads/2024/07/BYMA-CIR3569-Anexo-v20240704.pdf
# sha256 811dec4cebd6d0480fcbf140e285117bc23fead3b05fa81a932191c3643c19ad
TICK_SCHEDULE_ID = "byma_cir3569_anexo_v20240704"
TICK_SCHEDULE_EFFECTIVE = date(2024, 7, 4)
EQUITY_TICK_BANDS: Tuple[Tuple[Decimal, Decimal], ...] = (
    # (upper price bound inclusive, minimum price change)
    (Decimal("1"), Decimal("0.001")),
    (Decimal("5"), Decimal("0.005")),
    (Decimal("10"), Decimal("0.01")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.10")),
    (Decimal("250"), Decimal("0.25")),
    (Decimal("500"), Decimal("0.50")),
    (Decimal("1000"), Decimal("1.00")),
    (Decimal("5000"), Decimal("5.00")),
    (Decimal("10000"), Decimal("10.00")),
    (Decimal("50000"), Decimal("25.00")),
)
EQUITY_TICK_ABOVE = Decimal("50.00")

DEFAULT_LOT_SIZE = 1
# Yahoo truncates shortName at 31 characters; a CEDEAR marker may be cut off.
_YAHOO_SHORT_NAME_LIMIT = 31
_CEDEAR_MARKER = re.compile(r"\bCEDEARS?\b", re.I)
# A truncated tail that could be the start of "CEDEAR" (e.g. "... TR CE").
_TRUNCATED_MARKER = re.compile(r"\bC(E(D(E(A(R)?)?)?)?)?$", re.I)
SESSION_TAIL_WEEKDAYS = 10


def equity_tick(price: Decimal) -> Decimal:
    """Minimum price change for a BYMA equity/CEDEAR quoted at *price*."""
    for upper, tick in EQUITY_TICK_BANDS:
        if price <= upper:
            return tick
    return EQUITY_TICK_ABOVE


def classify_short_name(short_name: Optional[str]) -> Tuple[Optional[str], str]:
    """Share/CEDEAR from Yahoo's descriptor; ``None`` when it cannot tell.

    Positive "CEDEAR" text means CEDEAR. A name without it counts as a local
    share only when it is complete; a name at Yahoo's truncation limit, or one
    ending in what could be a cut-off "CEDEAR", is ambiguous.
    """
    name = (short_name or "").strip()
    if not name:
        return None, "no Yahoo descriptor"
    if _CEDEAR_MARKER.search(name):
        return "cedear", f"Yahoo shortName {name!r} names a CEDEAR"
    if len(name) >= _YAHOO_SHORT_NAME_LIMIT or _TRUNCATED_MARKER.search(name):
        return None, f"Yahoo shortName {name!r} may be truncated"
    return "share", f"Yahoo shortName {name!r} has no CEDEAR marker"


def yahoo_short_name(symbol: str) -> Optional[str]:
    """Read Yahoo's shortName through the existing quote-summary client."""
    from backtest.loaders import yahoo_client

    try:
        summary = yahoo_client.get_quote_summary(symbol, ["quoteType"])
    except Exception:  # noqa: BLE001 - an unreachable descriptor is "unknown"
        return None
    quote = summary.get("quoteType") if isinstance(summary, dict) else None
    name = quote.get("shortName") if isinstance(quote, dict) else None
    return name if isinstance(name, str) else None


def observed_sessions(
    frames: Iterable[pd.DataFrame], tail_weekdays: int = SESSION_TAIL_WEEKDAYS,
) -> Tuple[Tuple[date, ...], Dict[str, object]]:
    """Sessions = dates any loaded BYMA bar actually traded, plus a tail.

    Conservative: a day the market was open but no loaded instrument traded
    is treated as closed, which can only delay settlement. Zero-volume
    placeholder bars (holidays) never count. The tail of weekdays after the
    last traded day only dates the settlement of the final trades; it does
    not know future holidays and is reported as an assumption.
    """
    traded = set()
    for frame in frames:
        volume = pd.to_numeric(frame["volume"], errors="coerce")
        traded.update(ts.date() for ts, v in volume.items() if v > 0)
    if not traded:
        raise ValueError(
            "no traded BYMA bars to derive sessions from; declare "
            "ar_settlement_sessions"
        )
    observed = sorted(traded)
    tail = []
    day = observed[-1]
    while len(tail) < tail_weekdays:
        day += timedelta(days=1)
        if day.weekday() < 5:
            tail.append(day)
    provenance = {
        "kind": "derived",
        "method": "dates with traded volume in the loaded bars",
        "first": str(observed[0]),
        "last": str(observed[-1]),
        "observed_count": len(observed),
        "tail_assumption": f"{tail_weekdays} weekdays after {observed[-1]} (holidays unknown)",
    }
    return tuple(observed + tail), provenance


def resolve_instrument(
    symbol: str,
    declared: Optional[dict],
    *,
    describe: Callable[[str], Optional[str]] = yahoo_short_name,
) -> Tuple[dict, Dict[str, dict], list]:
    """Complete one instrument's metadata; return (metadata, provenance, problems).

    Declared fields win. Missing ``kind`` is classified from Yahoo; a CEDEAR
    is never completed automatically because its dated ratio history (with
    credit dates) has no automatic official source.
    """
    info = dict(declared or {})
    provenance: Dict[str, dict] = {}
    problems: list = []
    for key in info:
        provenance[key] = {"kind": "declared"}
    if "kind" not in info:
        kind, evidence = classify_short_name(describe(symbol))
        if kind == "share":
            info["kind"] = "share"
            provenance["kind"] = {"kind": "third_party", "source": "yahoo_quote_summary",
                                  "evidence": evidence}
        elif kind == "cedear":
            problems.append(
                f"{symbol}: Yahoo identifies a CEDEAR ({evidence}); declare "
                "ar_instruments.{symbol} with kind='cedear', underlying and a dated "
                "ratio_history (effective_date, ratio, credit_date for in-window changes) "
                "from the issuer's notices".replace("{symbol}", symbol)
            )
        else:
            problems.append(
                f"{symbol}: share/CEDEAR could not be established ({evidence}); declare "
                f"ar_instruments.{symbol}.kind as 'share' or 'cedear'"
            )
    if "currency" not in info:
        info["currency"] = "ARS"
        provenance["currency"] = {"kind": "derived",
                                  "source": ".BA ARS pool contract, checked against the loader's declared quote currency"}
    if "lot_size" not in info:
        info["lot_size"] = DEFAULT_LOT_SIZE
        provenance["lot_size"] = {
            "kind": "assumption",
            "note": "1 share per order; BYMA CIR 3569 defines the minimum as the listing "
                    "authorization's quantity without a number here",
        }
    if "tick_size" not in info:
        provenance["tick_size"] = {
            "kind": "official", "source": TICK_SCHEDULE_ID,
            "note": f"price-band schedule; official from {TICK_SCHEDULE_EFFECTIVE}, "
                    "applied as an assumption to earlier dates",
        }
    return info, provenance, problems
