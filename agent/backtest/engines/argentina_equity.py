"""Conservative BYMA cash-equity rule book (shares and CEDEARs).

This module deliberately does NOT activate .BA backtests in runner/composite.
The base engine currently lacks the corporate-action, settlement cash ledger and
point-in-time CEDEAR metadata required for reliable historical total returns.
Keep the existing fail-closed routing until those contracts are implemented.

No brokerage defaults are represented as official BYMA rates.
"""

from __future__ import annotations

import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

import pandas as pd

from backtest.engines.base import BaseEngine
from backtest.engines.vietnam_equity import newest_opening_bar_idx


class ArgentinaEquityEngine(BaseEngine):
    """Execution primitives for BYMA shares and CEDEARs.

    Explicit instrument metadata is mandatory. Example::

        ar_instruments = {
            "YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1,
                        "tick_size": "0.05"},
            "AAPL.BA": {"kind": "cedear", "currency": "ARS", "lot_size": 1,
                        "tick_size": "0.05"},
        }

    Tick sizes in the example are *illustrative*, not an assertion about
    actual BYMA tick schedules. Users must supply documented values.

    Costs are fractions of traded notional plus a fixed charge per order.
    """

    def __init__(self, config: dict):
        super().__init__({**config, "leverage": 1.0})
        instruments = config.get("ar_instruments")
        if not isinstance(instruments, dict) or not instruments:
            raise ValueError("ar_instruments must declare BYMA instrument metadata")
        self.instruments = {}
        self.ratio_changes = {}
        for symbol, info in instruments.items():
            if not isinstance(info, dict) or info.get("kind") not in ("share", "cedear"):
                raise ValueError(f"{symbol}: explicit share/cedear classification required")
            if info.get("currency") not in ("ARS", "USD"):
                raise ValueError(f"{symbol}: explicit ARS/USD quote currency required")
            lot = info.get("lot_size")
            if isinstance(lot, bool) or not isinstance(lot, int) or lot < 1:
                raise ValueError(f"{symbol}: positive integer lot_size required")
            try:
                tick = Decimal(str(info["tick_size"]))
            except (KeyError, ValueError, ArithmeticError):
                raise ValueError(f"{symbol}: positive tick_size required") from None
            if not tick.is_finite() or tick <= 0:
                raise ValueError(f"{symbol}: positive finite tick_size required")
            if not symbol.upper().endswith(".BA"):
                raise ValueError(f"{symbol}: expected a .BA listing")
            if info["kind"] == "cedear":
                if not info.get("underlying"):
                    raise ValueError(f"{symbol}: CEDEAR underlying required")
                ratios = info.get("ratio_history")
                if not isinstance(ratios, list) or not ratios:
                    raise ValueError(f"{symbol}: dated ratio_history required")
                previous = ""
                for item in ratios:
                    if not isinstance(item, dict) or "effective_date" not in item or "ratio" not in item:
                        raise ValueError(f"{symbol}: invalid ratio entry")
                    date = str(item["effective_date"])
                    try:
                        valid_date = pd.Timestamp(date).strftime("%Y-%m-%d") == date
                        ratio = Decimal(str(item["ratio"]))
                    except (ValueError, TypeError, ArithmeticError):
                        raise ValueError(f"{symbol}: invalid ratio entry") from None
                    if not valid_date or not ratio.is_finite() or ratio <= 0 or date <= previous:
                        raise ValueError(f"{symbol}: ratio history must be valid and increasing")
                    previous = date
            self.instruments[symbol.upper()] = {**info, "tick_size": tick}
            if info["kind"] == "cedear":
                self.ratio_changes[symbol.upper()] = tuple(
                    (item["effective_date"], Decimal(str(item["ratio"])))
                    for item in info["ratio_history"]
                )

        currencies = {x["currency"] for x in self.instruments.values()}
        if len(currencies) != 1:
            raise ValueError("BYMA engine has a single cash ledger; mixed ARS/USD is unsupported")
        self.currency = next(iter(currencies))
        self.slippage_rate = self._nonnegative(config.get("ar_slippage", 0.0), "ar_slippage")
        self.brokerage_rate = self._nonnegative(config.get("ar_brokerage_rate", 0.0), "ar_brokerage_rate")
        self.exchange_fee_rate = self._nonnegative(config.get("ar_exchange_fee_rate", 0.0), "ar_exchange_fee_rate")
        self.fixed_fee = self._nonnegative(config.get("ar_fixed_fee", 0.0), "ar_fixed_fee")

    @staticmethod
    def _nonnegative(value, key: str) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be finite and nonnegative") from None
        if not math.isfinite(parsed) or parsed < 0:
            raise ValueError(f"{key} must be finite and nonnegative")
        return parsed

    def _instrument(self, symbol: str) -> dict:
        try:
            return self.instruments[symbol.upper()]
        except KeyError:
            raise ValueError(f"{symbol}: unregistered BYMA instrument") from None

    def ratio_at(self, symbol: str, as_of: str) -> Decimal:
        """Look up a CEDEAR ratio without using future metadata."""
        instrument = self._instrument(symbol)
        if instrument["kind"] != "cedear":
            raise ValueError(f"{symbol}: not a CEDEAR")
        try:
            date = pd.Timestamp(as_of).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            raise ValueError("invalid as_of date") from None
        matches = [ratio for effective, ratio in self.ratio_changes[symbol.upper()]
                   if effective <= date]
        if not matches:
            raise ValueError(f"{symbol}: no effective ratio at {date}")
        return matches[-1]

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        return argentina_can_execute(self, self, symbol, direction, bar)
        # Conservative long-only cash-account model. A direction of 0 closes.
        if direction not in (0, 1):
            return False
        price = bar.get("close")
        try:
            return math.isfinite(float(price)) and float(price) > 0
        except (TypeError, ValueError):
            return False

    def round_size(self, raw_size: float, price: float) -> float:
        instrument = self._instrument(self._active_symbol)
        if not math.isfinite(raw_size) or raw_size <= 0:
            return 0.0
        lot = instrument["lot_size"]
        return float(math.floor(raw_size / lot) * lot)

    def calc_commission(self, size: float, price: float, direction: int, is_open: bool) -> float:
        self._instrument(self._active_symbol)
        notional = size * price
        if not math.isfinite(notional) or notional <= 0:
            return 0.0
        return notional * (self.brokerage_rate + self.exchange_fee_rate) + self.fixed_fee

    def apply_slippage(self, price: float, direction: int) -> float:
        instrument = self._instrument(self._active_symbol)
        if not math.isfinite(price) or price <= 0:
            raise ValueError("BYMA execution price must be positive and finite")
        if direction not in (-1, 0, 1):
            raise ValueError("invalid order direction")
        side = 1 if direction == 1 else -1
        slipped = Decimal(str(price)) * (Decimal(1) + side * Decimal(str(self.slippage_rate)))
        tick = instrument["tick_size"]
        rounding = ROUND_CEILING if side > 0 else ROUND_FLOOR
        return float((slipped / tick).to_integral_value(rounding=rounding) * tick)


def argentina_can_execute(state: BaseEngine, rules: ArgentinaEquityEngine, symbol: str, direction: int, bar: pd.Series) -> bool:
    """Shared state/rules hook for standalone and composite execution.

    Do not use this helper to enable .BA routing yet: settlement cash,
    point-in-time corporate actions and a verified calendar remain mandatory.
    Reuse Vietnam's fill-ledger lookup to avoid trusting the first entry bar
    after scale-ins; actual sell eligibility requires a dated BYMA calendar.
    """
    rules._instrument(symbol)
    if direction not in (0, 1):
        return False
    try:
        price = float(bar.get("close"))
    except (TypeError, ValueError):
        return False
    if not math.isfinite(price) or price <= 0:
        return False
    if direction == 0 and symbol in state.positions:
        # Inspect the newest fill, but fail closed without a validated
        # settlement-date mapping (bar offsets are insufficient for BYMA).
        newest_opening_bar_idx(state, symbol)
        return False
    return direction == 1
