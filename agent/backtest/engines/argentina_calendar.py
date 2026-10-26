"""Officially documented BYMA closures, used only past the last observed bar.

Inside a run the session calendar is the days the loaded bars actually traded
(conservative). After the last traded bar the loaded data says nothing, yet
the final trades still settle there: a sale on 2024-03-27 under T+2 settles
on 2024-04-04 because 03-28 .. 04-02 were closed, not on 03-29 as a plain
weekday tail claims. A year listed here extends the tail with official
business days; for any other year the tail is a weekday assumption whose
dates the engine does not report as settlement dates.

2024 closures (no trading, no settlement), weekdays only:
- National holidays, Ley 27.399 (Jan 1, Carnival, Holy Thursday/Good Friday,
  Malvinas, May 1, Güemes moved to Jun 17, Belgrano, Independence,
  Sovereignty moved to Nov 18, Christmas) and the 2024 tourism bridge days,
  Decreto 106/2023 (Apr 1, Jun 21, Oct 11). The government pages were not
  retrievable (HTTP 403); every one of these dates is corroborated by the
  absence of any BYMA trade in GGAL.BA, YPFD.BA and PAMP.BA that day, and
  those are the only 2024 weekdays without trades.
- 2024-12-31: BYMA Comunicado 18674 (2024-12-13): "no habrá actividades de
  negociación ni liquidación". https://data-widgets.byma.com.ar/wp-content/
  uploads/2024/12/BYMA-COM-18674-Actividades-en-BYMA-24-y-31-de-diciembre.pdf
  sha256 e45199f6b6435642b3dd6eb594b1c6e1766d8dd8c88f1e320769f8df6ab162a1.

Not modelled: days with trading but no settlement (2024-12-24 per the same
notice); see the plan's pending list.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Tuple

OFFICIAL_CLOSURES: Dict[int, Dict[date, str]] = {
    2024: {
        date(2024, 1, 1): "Año Nuevo (Ley 27.399)",
        date(2024, 2, 12): "Carnaval (Ley 27.399)",
        date(2024, 2, 13): "Carnaval (Ley 27.399)",
        date(2024, 3, 28): "Jueves Santo (Ley 27.399)",
        date(2024, 3, 29): "Viernes Santo (Ley 27.399)",
        date(2024, 4, 1): "Feriado con fines turísticos (Decreto 106/2023)",
        date(2024, 4, 2): "Malvinas (Ley 27.399)",
        date(2024, 5, 1): "Día del Trabajador (Ley 27.399)",
        date(2024, 6, 17): "Güemes, trasladado (Ley 27.399)",
        date(2024, 6, 20): "Belgrano (Ley 27.399)",
        date(2024, 6, 21): "Feriado con fines turísticos (Decreto 106/2023)",
        date(2024, 7, 9): "Independencia (Ley 27.399)",
        date(2024, 10, 11): "Feriado con fines turísticos (Decreto 106/2023)",
        date(2024, 11, 18): "Soberanía Nacional, trasladado (Ley 27.399)",
        date(2024, 12, 25): "Navidad (Ley 27.399)",
        date(2024, 12, 31): "BYMA Comunicado 18674: sin negociación ni liquidación",
    },
}


def official_closure(day: date) -> str | None:
    """The documented reason BYMA was closed on *day*, if listed."""
    return OFFICIAL_CLOSURES.get(day.year, {}).get(day)


def tail_sessions(last: date, count: int) -> Tuple[Tuple[date, ...], Tuple[date, ...]]:
    """``count`` sessions after *last*: (all, unverified).

    A weekday in a documented year is a session unless it is an official
    closure; a weekday in any other year is kept only as an unverified
    assumption, so callers can refuse to report it as a settlement date.
    """
    days: list[date] = []
    unverified: list[date] = []
    day = last
    while len(days) < count:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        if day.year in OFFICIAL_CLOSURES:
            if day in OFFICIAL_CLOSURES[day.year]:
                continue
        else:
            unverified.append(day)
        days.append(day)
    return tuple(days), tuple(unverified)
