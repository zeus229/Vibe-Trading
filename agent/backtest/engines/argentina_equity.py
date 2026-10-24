"""Conservative BYMA cash-equity rule book (shares and CEDEARs).

This module deliberately does NOT activate .BA backtests in runner/composite.
The base engine currently lacks the corporate-action, settlement cash ledger and
point-in-time CEDEAR metadata required for reliable historical total returns.
Keep the existing fail-closed routing until those contracts are implemented.

No brokerage defaults are represented as official BYMA rates.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

import pandas as pd

from backtest.engines.base import BaseEngine
from backtest.engines.argentina_settlement import settlement_date
from backtest.engines.argentina_reconciliation import available_for_settlement, pending_sale_proceeds
from backtest.engines.argentina_data import (
    DEFAULT_MAX_UNEXPLAINED_CEDEAR_MOVE,
    DEFAULT_MAX_UNEXPLAINED_SHARE_MOVE,
    DEFAULT_RATIO_JUMP_TOLERANCE,
    PRICE_SERIES_DECLARATIONS,
    drop_non_session_bars,
    reconcile_ratio_changes,
    unexplained_discontinuities,
    validate_frame_contract,
)


class ArgentinaEquityEngine(BaseEngine):
    """Execution primitives for BYMA shares and CEDEARs.

    Explicit instrument metadata is mandatory. Example::

        ar_instruments = {
            "YPFD.BA": {"kind": "share", "currency": "ARS", "lot_size": 1,
                        "tick_size": "0.05"},
            "AAPL.BA": {"kind": "cedear", "currency": "ARS", "lot_size": 1,
                        "tick_size": "0.05", "underlying": "AAPL",
                        "ratio_history": [{"effective_date": "2020-01-01", "ratio": "10"}]},
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
                    effective = str(item["effective_date"])
                    try:
                        valid_date = pd.Timestamp(effective).strftime("%Y-%m-%d") == effective
                        ratio = Decimal(str(item["ratio"]))
                    except (ValueError, TypeError, ArithmeticError):
                        raise ValueError(f"{symbol}: invalid ratio entry") from None
                    if not valid_date or not ratio.is_finite() or ratio <= 0 or effective <= previous:
                        raise ValueError(f"{symbol}: ratio history must be valid and increasing")
                    if item.get("price_series") not in PRICE_SERIES_DECLARATIONS:
                        raise ValueError(
                            f"{symbol}: price_series must be 'adjusted' or 'unadjusted'"
                        )
                    previous = effective
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
        calendar = config.get("ar_settlement_sessions")
        if calendar is None:
            self.settlement_sessions = None
        else:
            if not isinstance(calendar, (list, tuple)) or not calendar or any(type(x) is not date for x in calendar):
                raise ValueError("ar_settlement_sessions must contain explicit date objects")
            if list(calendar) != sorted(set(calendar)):
                raise ValueError("ar_settlement_sessions must be strictly increasing")
            self.settlement_sessions = tuple(calendar)
        self.slippage_rate = self._nonnegative(config.get("ar_slippage", 0.0), "ar_slippage")
        self.brokerage_rate = self._nonnegative(config.get("ar_brokerage_rate", 0.0), "ar_brokerage_rate")
        self.exchange_fee_rate = self._nonnegative(config.get("ar_exchange_fee_rate", 0.0), "ar_exchange_fee_rate")
        self.fixed_fee = self._nonnegative(config.get("ar_fixed_fee", 0.0), "ar_fixed_fee")
        self.ratio_jump_tolerance = self._nonnegative(
            config.get("ar_ratio_jump_tolerance", DEFAULT_RATIO_JUMP_TOLERANCE),
            "ar_ratio_jump_tolerance",
        )
        self.max_unexplained_cedear_move = self._nonnegative(
            config.get("ar_max_unexplained_cedear_move", DEFAULT_MAX_UNEXPLAINED_CEDEAR_MOVE),
            "ar_max_unexplained_cedear_move",
        )
        self.max_unexplained_share_move = self._nonnegative(
            config.get("ar_max_unexplained_share_move", DEFAULT_MAX_UNEXPLAINED_SHARE_MOVE),
            "ar_max_unexplained_share_move",
        )
        for key, value in (("ar_max_unexplained_cedear_move", self.max_unexplained_cedear_move),
                           ("ar_max_unexplained_share_move", self.max_unexplained_share_move)):
            if not 0 < value < 1:
                raise ValueError(f"{key} must be between 0 and 1")
        self.ratio_adjustments: list[dict] = []
        self.dropped_non_session_bars: dict[str, int] = {}

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
            as_of_day = pd.Timestamp(as_of).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            raise ValueError("invalid as_of date") from None
        matches = [ratio for effective, ratio in self.ratio_changes[symbol.upper()]
                   if effective <= as_of_day]
        if not matches:
            raise ValueError(f"{symbol}: no effective ratio at {as_of_day}")
        return matches[-1]

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        return argentina_can_execute(self, self, symbol, direction, bar)

    def prepare_market_data(self, data_map: dict) -> dict:
        """Hold loaded bars to one declared, consistent BYMA price basis.

        Requires the loader's declared quote currency to match metadata,
        drops zero-volume placeholder bars on non-sessions, reconciles every
        declared CEDEAR ratio change with the observed prices, and rejects a
        large CEDEAR move no declared change explains. Fails closed.
        """
        if self.settlement_sessions is None:
            raise ValueError("BYMA data validation requires a verified settlement calendar")
        prepared = {}
        self.ratio_adjustments = []
        self.dropped_non_session_bars = {}
        for symbol, frame in data_map.items():
            instrument = self._instrument(symbol)
            validate_frame_contract(frame, symbol, instrument["currency"])
            attrs = dict(frame.attrs)
            frame, dropped = drop_non_session_bars(frame, symbol, self.settlement_sessions)
            if dropped:
                self.dropped_non_session_bars[symbol] = dropped
            if instrument["kind"] == "cedear":
                frame, events = reconcile_ratio_changes(
                    frame, symbol, instrument["ratio_history"],
                    tolerance=self.ratio_jump_tolerance,
                )
                self.ratio_adjustments.extend(events)
                jumps = unexplained_discontinuities(frame, self.max_unexplained_cedear_move)
                if jumps:
                    raise ValueError(
                        f"{symbol}: unexplained CEDEAR price discontinuities on "
                        f"{', '.join(jumps[:5])}; declare the ratio change in "
                        "ratio_history (effective_date, ratio) or exclude the window"
                    )
            else:
                jumps = unexplained_discontinuities(frame, self.max_unexplained_share_move)
                if jumps:
                    raise ValueError(
                        f"{symbol}: price discontinuities on {', '.join(jumps[:5])} "
                        "look like an unadjusted split; use a split-adjusted source "
                        "or exclude the window"
                    )
            frame.attrs.update(attrs)
            prepared[symbol] = frame
        return prepared

    def available_cash(self, timestamp: pd.Timestamp) -> float:
        """Buying power for normal BYMA settlement, including matching sales."""
        if self.settlement_sessions is None:
            raise ValueError("BYMA buying power requires a verified settlement calendar")
        today = _trade_date(timestamp)
        if today is None:
            raise ValueError("BYMA buying power requires a session timestamp")
        purchase_due = settlement_date(today, self.settlement_sessions)
        return available_for_settlement(
            self.capital, self.fill_records, today, purchase_due,
            self.settlement_sessions,
        )

    def projected_reduction_buying_power(
        self, reductions: list, timestamp: pd.Timestamp,
    ) -> float:
        """Match sales and purchases with the same normal settlement date.

        This assumes one validated currency/clearing pool and normal-cash
        settlement, not same-day instant cash or permission to withdraw.
        """
        if self.settlement_sessions is None:
            raise ValueError("BYMA netting requires a verified settlement calendar")
        today = _trade_date(timestamp)
        if today is None:
            raise ValueError("BYMA netting requires a valid session date")
        settlement_date(today, self.settlement_sessions)
        return sum(order.capital_credit for order in reductions)

    def can_liquidate_at_end(self, symbol: str, timestamp: pd.Timestamp) -> bool:
        """The terminal close obeys the same sell-settlement rule as signals."""
        today = _trade_date(timestamp)
        sessions = self.settlement_sessions
        if today is None or sessions is None:
            return False
        return argentina_sell_is_settled(self, symbol, today, sessions)

    def _engine_diagnostics(self) -> dict:
        """Declare what the terminal equity includes beyond settled cash."""
        if not self.equity_snapshots:
            return {}
        last_day = _trade_date(self.equity_snapshots[-1].timestamp)
        sessions = self.settlement_sessions
        if last_day is None or sessions is None:
            return {}
        receivable = pending_sale_proceeds(self.fill_records, last_day, sessions)
        return {
            "ar_price_basis": "source_split_dividend_adjusted_ohlc_raw_volume",
            "ar_cedear_ratio_adjustments": list(self.ratio_adjustments),
            "ar_dropped_non_session_bars": dict(self.dropped_non_session_bars),
            "ar_unsettled_sale_receivable_at_end": round(receivable, 6),
            "ar_unliquidated_position_count": len(self.unliquidated_positions),
            "ar_unliquidated_positions": [
                {k: (round(v, 6) if isinstance(v, float) else v) for k, v in item.items()}
                for item in self.unliquidated_positions
            ],
            "ar_terminal_equity_basis": (
                "unsettled_positions_marked_net_of_estimated_exit_costs"
                if self.unliquidated_positions
                else "all_positions_liquidated_at_last_close"
            ),
        }

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


def _trade_date(value) -> date | None:
    """Extract a session date only from an explicit timestamp or date."""
    if type(value) is date:
        return value
    if isinstance(value, (datetime, pd.Timestamp)) and not pd.isna(value):
        return value.date()
    return None


def argentina_sell_is_settled(
    state: BaseEngine, symbol: str, today: date, sessions: tuple[date, ...],
) -> bool:
    """Require the newest opening fill of the held position to be settled.

    BaseEngine compresses scale-ins into one position without per-lot holdings,
    so this conservatively blocks partial sells until the newest lot settles.
    """
    if type(today) is not date or not sessions or today not in sessions:
        return False
    if symbol not in state.positions:
        return False
    opening = None
    for fill in reversed(getattr(state, "fill_records", ()) or ()):
        if fill.symbol == symbol and fill.action in ("open", "increase"):
            opening = fill
            break
    if opening is None:
        return False
    opened = _trade_date(opening.timestamp)
    if opened is None:
        return False
    try:
        return settlement_date(opened, sessions) <= today
    except (TypeError, ValueError):
        return False


def argentina_can_execute(
    state: BaseEngine, rules: ArgentinaEquityEngine,
    symbol: str, direction: int, bar: pd.Series,
) -> bool:
    """State/rules helper shared by future standalone and composite routing.

    The runner remains blocked until settled-cash and corporate-action events
    integrate safely with BaseEngine.
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
    # A bar with no reported volume is a source placeholder (stale print,
    # holiday fill); its price was not executable.
    try:
        volume = float(bar.get("volume"))
    except (TypeError, ValueError):
        return False
    if not math.isfinite(volume) or volume <= 0:
        return False
    if direction == 1:
        return True
    today = _trade_date(bar.name)
    sessions = rules.settlement_sessions
    if today is None or sessions is None:
        return False
    return argentina_sell_is_settled(state, symbol, today, sessions)
