"""Conservative cash settlement ledger for BYMA normal-cash transactions.

The ledger tracks unsettled sales separately from spendable cash. Buys require
fully settled funds and reserve them immediately. No leverage, shorting,
cross-currency netting or implicit funding is permitted.

This is an isolated accounting component. The existing Vibe runner continues
to reject .BA execution until all event and data contracts are integrated.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Iterable

from backtest.engines.argentina_settlement import settlement_date


@dataclass(frozen=True)
class PendingCredit:
    settlement: date
    amount: float
    symbol: str


class ArgentinaCashLedger:
    """Single-currency, settled-cash-only execution accounting."""

    def __init__(self, initial_cash: float, currency: str):
        if currency not in ("ARS", "USD"):
            raise ValueError("BYMA ledger requires an explicit ARS/USD currency")
        if isinstance(initial_cash, bool) or not isfinite(initial_cash) or initial_cash < 0:
            raise ValueError("initial_cash must be finite and nonnegative")
        self.currency = currency
        self.settled_cash = float(initial_cash)
        self.pending_credits: list[PendingCredit] = []
        self.positions: dict[str, int] = {}
        self._last_date: date | None = None

    @staticmethod
    def _positive(value: float, label: str) -> None:
        if isinstance(value, bool) or not isfinite(value) or value <= 0:
            raise ValueError(f"{label} must be finite and positive")

    def advance(self, today: date) -> None:
        """Credit sales whose settlement date has arrived; never move backwards."""
        if not isinstance(today, date):
            raise TypeError("today must be a date")
        if self._last_date is not None and today < self._last_date:
            raise ValueError("ledger date cannot move backwards")
        self._last_date = today
        due = [item for item in self.pending_credits if item.settlement <= today]
        self.settled_cash += sum(item.amount for item in due)
        self.pending_credits = [item for item in self.pending_credits if item.settlement > today]

    def buy(
        self,
        symbol: str,
        quantity: int,
        price: float,
        fee: float,
        trade_date: date,
        sessions: Iterable[date],
    ) -> date:
        """Buy with settled funds; verify normal-cash settlement is schedulable."""
        self.advance(trade_date)
        if not symbol or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("positive integer buy quantity and symbol required")
        self._positive(price, "price")
        if isinstance(fee, bool) or not isfinite(fee) or fee < 0:
            raise ValueError("fee must be finite and nonnegative")
        settles = settlement_date(trade_date, sessions)
        debit = quantity * price + fee
        if not isfinite(debit) or debit > self.settled_cash:
            raise ValueError("insufficient settled cash for BYMA purchase")
        self.settled_cash -= debit
        self.positions[symbol] = self.positions.get(symbol, 0) + quantity
        return settles

    def sell(
        self,
        symbol: str,
        quantity: int,
        price: float,
        fee: float,
        trade_date: date,
        sessions: Iterable[date],
    ) -> date:
        """Reserve shares now; make proceeds spendable only at settlement."""
        self.advance(trade_date)
        if not symbol or isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("positive integer sell quantity and symbol required")
        self._positive(price, "price")
        if isinstance(fee, bool) or not isfinite(fee) or fee < 0:
            raise ValueError("fee must be finite and nonnegative")
        if self.positions.get(symbol, 0) < quantity:
            raise ValueError("short sales are not supported")
        settles = settlement_date(trade_date, sessions)
        proceeds = quantity * price - fee
        if not isfinite(proceeds) or proceeds < 0:
            raise ValueError("sale fees exceed proceeds")
        self.positions[symbol] -= quantity
        self.pending_credits.append(PendingCredit(settles, proceeds, symbol))
        return settles

    @property
    def pending_cash(self) -> float:
        return sum(item.amount for item in self.pending_credits)
