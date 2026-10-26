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
from backtest.engines.argentina_metadata import (
    TICK_SCHEDULE_EFFECTIVE,
    TICK_SCHEDULE_ID,
    equity_tick,
    observed_sessions,
    resolve_instrument,
    weekday_sessions,
)
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
        declared = config.get("ar_instruments")
        if declared is None:
            declared = {}
        if not isinstance(declared, dict):
            raise ValueError(_config_error(["ar_instruments must be an object keyed by .BA symbol"]))
        self.instruments = {}
        self.ratio_changes = {}
        self.metadata_provenance: dict[str, dict] = {}
        self._declared_instruments: dict[str, dict] = {}
        problems: list[str] = []
        for symbol, info in declared.items():
            if not isinstance(info, dict):
                problems.append(f"{symbol}: metadata must be an object")
                continue
            problems.extend(_field_problems(symbol, info))
            self._declared_instruments[symbol.upper()] = dict(info)
        if problems:
            raise ValueError(_config_error(problems))
        # Fully declared instruments are registered now; partial or missing
        # ones are completed from data and sources in prepare_market_data.
        for symbol, info in self._declared_instruments.items():
            if all(key in info for key in ("kind", "currency", "lot_size")):
                self._register(symbol, info, {key: {"kind": "declared"} for key in info})
        currencies = {x["currency"] for x in self.instruments.values()}
        if len(currencies) > 1:
            raise ValueError("BYMA engine has a single cash ledger; mixed ARS/USD is unsupported")
        self.currency = next(iter(currencies), None)
        calendar = config.get("ar_settlement_sessions")
        self.calendar_provenance: dict = {}
        if calendar is None:
            self.settlement_sessions = None
        else:
            if not isinstance(calendar, (list, tuple)) or not calendar:
                raise ValueError("ar_settlement_sessions must contain explicit session dates")
            calendar = [_parse_session(x, "ar_settlement_sessions") for x in calendar]
            if list(calendar) != sorted(set(calendar)):
                raise ValueError("ar_settlement_sessions must be strictly increasing")
            self.settlement_sessions = tuple(calendar)
            self.calendar_provenance = {"kind": "declared", "count": len(calendar)}
        # Calendar for the CEDEAR credit-window rule; differs only when derived.
        self.credit_check_sessions = self.settlement_sessions
        self.slippage_rate = self._nonnegative(config.get("ar_slippage", 0.0), "ar_slippage")
        # The generic `commission` rate every engine accepts is the brokerage
        # rate unless a BYMA-specific one is declared; never silently zero.
        if "ar_brokerage_rate" in config:
            self.brokerage_rate = self._nonnegative(config["ar_brokerage_rate"], "ar_brokerage_rate")
            brokerage_source = "ar_brokerage_rate"
        elif config.get("commission") is not None:
            self.brokerage_rate = self._nonnegative(config["commission"], "commission")
            brokerage_source = "commission"
        else:
            self.brokerage_rate = 0.0
            brokerage_source = "none_configured"
        self.exchange_fee_rate = self._nonnegative(config.get("ar_exchange_fee_rate", 0.0), "ar_exchange_fee_rate")
        self.fixed_fee = self._nonnegative(config.get("ar_fixed_fee", 0.0), "ar_fixed_fee")
        self.fee_basis = {
            # Configured or not, costs are simulation inputs, never official tariffs.
            "kind": "assumption",
            "brokerage_rate": self.brokerage_rate, "brokerage_source": brokerage_source,
            "exchange_fee_rate": self.exchange_fee_rate, "fixed_fee": self.fixed_fee,
            "slippage": self.slippage_rate,
            "note": "simulation costs supplied by configuration; not an official BYMA or broker tariff",
        }
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

    def _register(self, symbol: str, info: dict, provenance: dict) -> None:
        """Store validated metadata; ``tick_size`` None means the BYMA schedule."""
        tick = Decimal(str(info["tick_size"])) if info.get("tick_size") is not None else None
        self.instruments[symbol.upper()] = {**info, "tick_size": tick}
        self.metadata_provenance[symbol.upper()] = provenance
        if info["kind"] == "cedear":
            self.ratio_changes[symbol.upper()] = tuple(
                (item["effective_date"], Decimal(str(item["ratio"])))
                for item in info["ratio_history"]
            )

    def _resolve_loaded_instruments(self, symbols) -> None:
        """Complete metadata for every loaded symbol or fail with all gaps at once."""
        problems: list[str] = []
        for symbol in symbols:
            if symbol.upper() in self.instruments:
                continue
            if not symbol.upper().endswith(".BA"):
                problems.append(f"{symbol}: expected a .BA listing")
                continue
            info, provenance, gaps = resolve_instrument(
                symbol, self._declared_instruments.get(symbol.upper()),
                describe=self.describe_instrument,
            )
            problems.extend(gaps)
            if not gaps:
                field_gaps = _field_problems(symbol, info, require=True)
                problems.extend(field_gaps)
                if not field_gaps:
                    self._register(symbol, info, provenance)
        if problems:
            raise ValueError(_config_error(problems))
        currencies = {x["currency"] for x in self.instruments.values()}
        if len(currencies) > 1:
            raise ValueError("BYMA engine has a single cash ledger; mixed ARS/USD is unsupported")
        self.currency = next(iter(currencies), None)

    @staticmethod
    def describe_instrument(symbol: str):
        """Vendor descriptor used to tell shares from CEDEARs (Yahoo shortName)."""
        from backtest.engines.argentina_metadata import yahoo_short_name

        return yahoo_short_name(symbol)

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
        self._resolve_loaded_instruments(list(data_map))
        if self.settlement_sessions is None:
            for symbol, frame in data_map.items():
                validate_frame_contract(frame, symbol, self._instrument(symbol)["currency"])
            self.settlement_sessions, self.calendar_provenance = observed_sessions(
                data_map.values())
            # Missing sessions only delay settlement, which is conservative for
            # cash and securities but NOT for the credit window, where a later
            # settlement looks deliverable sooner. Count unknown weekdays as open.
            self.credit_check_sessions = weekday_sessions(self.settlement_sessions)
            self.calendar_provenance["credit_window_rule"] = (
                "unknown weekdays counted as sessions (earliest settlement)")
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
                credits = {str(item["effective_date"]): item.get("credit_date")
                           for item in instrument["ratio_history"]}
                for event in events:
                    credit = credits.get(event["effective_date"])
                    if credit is None:
                        raise ValueError(
                            f"{symbol}: ratio change on {event['effective_date']} needs the "
                            "depositary credit_date of the additional certificates; "
                            "positions held through it cannot be sold before then"
                        )
                    event["credit_date"] = str(_parse_session(credit, f"{symbol}: credit_date"))
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
        return argentina_available_cash(self, self, timestamp)

    def projected_reduction_buying_power(
        self, reductions: list, timestamp: pd.Timestamp,
    ) -> float:
        """Match sales and purchases with the same normal settlement date."""
        return argentina_reduction_buying_power(self, self, reductions, timestamp)

    def can_liquidate_at_end(self, symbol: str, timestamp: pd.Timestamp) -> bool:
        """The terminal close obeys the same sell rules as signals."""
        return argentina_sell_allowed(self, self, symbol, _trade_date(timestamp))

    def _engine_diagnostics(self) -> dict:
        """Declare what the terminal equity includes beyond settled cash."""
        return argentina_diagnostics(self, self)

    def _write_artifacts(self, run_dir, *args, **kwargs) -> None:
        super()._write_artifacts(run_dir, *args, **kwargs)
        write_byma_tables(run_dir, self, self)
        write_preparation_artifact(run_dir, argentina_diagnostics(self, self))

    def unit_scale(self, symbol: str, timestamp) -> Decimal:
        """Adjusted units per real certificate on *timestamp*'s session.

        Bars before an in-window ratio change were restated in post-change
        units, so one real pre-change certificate is ``new/old`` of them.
        """
        today = _trade_date(timestamp)
        scale = Decimal(1)
        if today is None:
            return scale
        for event in self.ratio_adjustments:
            if event["symbol"].upper() == symbol.upper() and date.fromisoformat(event["effective_date"]) > today:
                scale *= Decimal(str(event["new_ratio"])) / Decimal(str(event["old_ratio"]))
        return scale

    def round_size(self, raw_size: float, price: float) -> float:
        """Whole real lots: before a ratio change one lot spans ``new/old`` units."""
        instrument = self._instrument(self._active_symbol)
        if not math.isfinite(raw_size) or raw_size <= 0:
            return 0.0
        lot = Decimal(instrument["lot_size"]) * self.unit_scale(
            self._active_symbol, getattr(self, "_bar_ts", None))
        lots = (Decimal(repr(raw_size)) / lot).to_integral_value(rounding=ROUND_FLOOR)
        return float(lots * lot)

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
        # Real ticks apply to real certificates; adjusted prices before a
        # ratio change are ``old/new`` of them, and so is the tick.
        scale = self.unit_scale(self._active_symbol, getattr(self, "_bar_ts", None))
        real_tick = instrument["tick_size"]
        if real_tick is None:  # BYMA CIR 3569 band of the real certificate price
            real_tick = equity_tick(slipped * scale)
        tick = real_tick / scale
        rounding = ROUND_CEILING if side > 0 else ROUND_FLOOR
        return float((slipped / tick).to_integral_value(rounding=rounding) * tick)


_CONFIG_EXAMPLE = (
    'Example: "ar_instruments": {"YPFD.BA": {"kind": "share"}} (currency, lot_size, '
    "tick_size and the session calendar are completed automatically for shares and "
    "labelled in the ar_* diagnostics); CEDEARs also need \"underlying\" and "
    '\"ratio_history\": [{"effective_date": "YYYY-MM-DD", "ratio": "20", '
    '"credit_date": "YYYY-MM-DD"}]. Do not set "engine": .BA codes select the BYMA engine.'
)


def _data_basis_summary(rules: "ArgentinaEquityEngine", scheduled: list, early_fills: int) -> str:
    """One scalar line (survives stdout truncation) naming each value's evidence."""
    parts = []
    for symbol, info in sorted(rules.instruments.items()):
        prov = rules.metadata_provenance.get(symbol, {})

        def label(key, shown):
            return f"{key}={shown}[{prov.get(key, {}).get('kind', 'declared')}]"

        tick = "CIR3569 bands" if info.get("tick_size") is None else str(info["tick_size"])
        parts.append(f"{symbol}: " + ", ".join((
            label("kind", info["kind"]), label("currency", info["currency"]),
            label("lot_size", info["lot_size"]), label("tick_size", tick))))
    cal = rules.calendar_provenance or {}
    if cal.get("kind") == "derived":
        tail = ("official business days" if not cal.get("unverified_tail")
                else "unverified weekdays, settlement dates there not reported")
        parts.append(f"calendar=traded days {cal['first']}..{cal['last']} "
                     f"({cal['observed_count']}) [derived; tail: {tail}]")
    elif cal:
        parts.append(f"calendar={cal.get('count')} sessions [declared]")
    fee = rules.fee_basis
    parts.append(
        f"fees: brokerage {fee['brokerage_rate']:.4%} from {fee['brokerage_source']}, "
        f"exchange {fee['exchange_fee_rate']:.4%}, fixed {fee['fixed_fee']:g}, "
        f"slippage {fee['slippage']:.4%} [{fee['kind']}: simulation inputs, not official tariffs]"
    )
    if scheduled and early_fills:
        parts.append(f"CIR3569 v2024-07-04 ticks applied as an assumption to {early_fills} earlier fill(s)")
    parts.append("details: artifacts/byma_preparation.json")
    return "; ".join(parts)


def byma_cash_ledger(state: BaseEngine, rules: "ArgentinaEquityEngine") -> list[dict]:
    """Per-fill cash reconciliation from the engine's own fill ledger.

    BYMA runs have no cash movements besides fills, so replaying them from
    the initial capital reproduces the engine's cash exactly. Each purchase
    records the buying power the engine allowed for its settlement date
    (settled cash plus sales settling on/before it), which is what shows a
    same-day sale funding it without using the money twice.
    """
    sessions = rules.settlement_sessions
    cash = float(state.initial_capital)
    rows: list[dict] = []
    seen: list = []
    for fill in state.fill_records:
        day = _trade_date(fill.timestamp)
        qty = float(fill.signed_quantity)
        notional = abs(qty) * float(fill.execution_price)
        fee = float(fill.fee)
        try:
            settles = settlement_date(day, sessions) if sessions else None
        except (TypeError, ValueError):
            settles = None
        unverified = {date.fromisoformat(d) for d in rules.calendar_provenance.get("unverified_tail", ())}
        if settles is None:
            status = "unknown"
        elif any(day < d <= settles for d in unverified):
            # Counting T+N crossed a day the loaded data and the official
            # table cannot confirm as a session: the date is not reportable.
            status = "unknown_beyond_loaded_data"
        else:
            status = rules.calendar_provenance.get("kind", "declared")
        buying_power = None
        if qty > 0 and settles is not None:
            try:
                buying_power = available_for_settlement(cash, seen, day, settles, sessions)
            except (TypeError, ValueError):
                buying_power = None
        flow = -(notional + fee) if qty > 0 else notional - fee
        cash += flow
        seen.append(fill)
        unsettled = 0.0
        for prior in seen:
            if float(prior.signed_quantity) < 0:
                try:
                    if settlement_date(_trade_date(prior.timestamp), sessions) > day:
                        unsettled += abs(float(prior.signed_quantity)) * float(prior.execution_price) - float(prior.fee)
                except (TypeError, ValueError):
                    pass
        rows.append({
            "timestamp": str(day),
            "code": fill.symbol,
            "side": "buy" if qty > 0 else "sell",
            "qty": abs(qty),
            "notional": round(notional, 6),
            "fee": round(fee, 6),
            "total_cost": round(notional + fee, 6) if qty > 0 else "",
            "net_proceeds": round(notional - fee, 6) if qty < 0 else "",
            "cash_flow": round(flow, 6),
            "cash_after": round(cash, 6),
            "settlement_date": "" if status.startswith("unknown") else str(settles),
            "settlement_date_status": status,
            "buying_power_before": "" if buying_power is None else round(buying_power, 6),
            "unsettled_sale_proceeds_after": round(unsettled, 6),
        })
    return rows


def byma_monthly_equity(state: BaseEngine) -> list[dict]:
    """Month-end equity and returns from the engine's equity snapshots."""
    rows: list[dict] = []
    previous = float(state.initial_capital)
    snapshots = state.equity_snapshots
    for index, snap in enumerate(snapshots):
        month = pd.Timestamp(snap.timestamp).strftime("%Y-%m")
        nxt = snapshots[index + 1] if index + 1 < len(snapshots) else None
        if nxt is not None and pd.Timestamp(nxt.timestamp).strftime("%Y-%m") == month:
            continue
        equity = float(snap.equity)
        rows.append({
            "date": pd.Timestamp(snap.timestamp).strftime("%Y-%m-%d"),
            "month": month,
            "equity_end": round(equity, 6),
            "month_return": round(equity / previous - 1, 10) if previous else "",
            "cumulative_return": round(equity / float(state.initial_capital) - 1, 10),
        })
        previous = equity
    return rows


def write_byma_tables(run_dir, state: BaseEngine, rules: "ArgentinaEquityEngine") -> None:
    """Write cash_ledger.csv and monthly_equity.csv as engine artifacts."""
    from pathlib import Path

    out = Path(run_dir) / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    for name, rows, columns in (
        ("cash_ledger.csv", byma_cash_ledger(state, rules),
         ["timestamp", "code", "side", "qty", "notional", "fee", "total_cost", "net_proceeds",
          "cash_flow", "cash_after",
          "settlement_date", "settlement_date_status", "buying_power_before",
          "unsettled_sale_proceeds_after"]),
        ("monthly_equity.csv", byma_monthly_equity(state),
         ["date", "month", "equity_end", "month_return", "cumulative_return"]),
    ):
        pd.DataFrame(rows, columns=columns).to_csv(out / name, index=False)


def write_preparation_artifact(run_dir, diagnostics: dict) -> None:
    """Persist the full BYMA provenance next to the run's other artifacts."""
    import json
    from pathlib import Path

    keys = ("ar_data_basis", "settlement_currency", "ar_net_pnl", "ar_total_fees", "ar_buy_fees",
            "ar_sell_fees", "ar_fill_count", "ar_metadata_provenance", "ar_calendar_provenance",
            "ar_tick_schedule", "ar_fee_basis", "ar_cedear_ratio_adjustments",
            "ar_dropped_non_session_bars", "ar_terminal_equity_basis",
            "ar_unliquidated_positions", "ar_unsettled_sale_receivable_at_end")
    out = Path(run_dir) / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    payload = {k: diagnostics[k] for k in keys if k in diagnostics}
    (out / "byma_preparation.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _config_error(problems: list) -> str:
    """One message with every BYMA configuration gap and the expected shape."""
    lines = "\n".join(f"- {p}" for p in problems)
    return f"BYMA backtest configuration incomplete:\n{lines}\n{_CONFIG_EXAMPLE}"


def _field_problems(symbol: str, info: dict, *, require: bool = False) -> list:
    """Validate declared BYMA fields; with *require*, also report missing ones."""
    problems = []
    if not symbol.upper().endswith(".BA"):
        problems.append(f"{symbol}: expected a .BA listing")
    kind = info.get("kind")
    if "kind" in info and kind not in ("share", "cedear"):
        problems.append(f"{symbol}.kind must be 'share' or 'cedear' (got {kind!r})")
    elif require and kind is None:
        problems.append(f"{symbol}.kind is required ('share' or 'cedear')")
    if "currency" in info and info["currency"] not in ("ARS", "USD"):
        problems.append(f"{symbol}.currency must be 'ARS' or 'USD'")
    elif require and "currency" not in info:
        problems.append(f"{symbol}.currency is required")
    if "lot_size" in info:
        lot = info["lot_size"]
        if isinstance(lot, bool) or not isinstance(lot, int) or lot < 1:
            problems.append(f"{symbol}.lot_size must be a positive integer")
    elif require:
        problems.append(f"{symbol}.lot_size is required")
    if info.get("tick_size") is not None:
        try:
            tick = Decimal(str(info["tick_size"]))
            if not tick.is_finite() or tick <= 0:
                raise ArithmeticError
        except (ValueError, ArithmeticError):
            problems.append(f"{symbol}.tick_size must be a positive number (omit it to use BYMA CIR 3569 bands)")
    if kind == "cedear":
        if not info.get("underlying"):
            problems.append(f"{symbol}.underlying is required for a CEDEAR")
        ratios = info.get("ratio_history")
        if not isinstance(ratios, list) or not ratios:
            problems.append(f"{symbol}.ratio_history (dated CEDEAR ratios) is required")
        else:
            previous = ""
            for item in ratios:
                if not isinstance(item, dict) or "effective_date" not in item or "ratio" not in item:
                    problems.append(f"{symbol}.ratio_history entries need effective_date and ratio")
                    break
                effective = str(item["effective_date"])
                try:
                    valid_date = pd.Timestamp(effective).strftime("%Y-%m-%d") == effective
                    ratio = Decimal(str(item["ratio"]))
                except (ValueError, TypeError, ArithmeticError):
                    problems.append(f"{symbol}: invalid ratio entry {item!r}")
                    break
                if not valid_date or not ratio.is_finite() or ratio <= 0 or effective <= previous:
                    problems.append(f"{symbol}: ratio history must be valid and increasing")
                    break
                if item.get("price_series") not in PRICE_SERIES_DECLARATIONS:
                    problems.append(f"{symbol}: price_series must be 'adjusted' or 'unadjusted'")
                    break
                if "credit_date" in item:
                    try:
                        credit = _parse_session(item["credit_date"], f"{symbol}: credit_date")
                    except ValueError as exc:
                        problems.append(str(exc))
                        break
                    if credit < date.fromisoformat(effective):
                        problems.append(f"{symbol}: credit_date precedes effective_date")
                        break
                previous = effective
    return problems


def _parse_session(value, label: str) -> date:
    """Accept a ``date`` or a strict ISO ``YYYY-MM-DD`` string (JSON configs)."""
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            parsed = None
        if parsed is not None and parsed.isoformat() == value:
            return parsed
    raise ValueError(f"{label} must contain explicit session dates (YYYY-MM-DD)")


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


def _lifecycle_openings(state: BaseEngine, symbol: str) -> list:
    """Opening fills of the currently held position (since its last close)."""
    openings = []
    for fill in reversed(getattr(state, "fill_records", ()) or ()):
        if fill.symbol != symbol:
            continue
        if fill.action == "close":
            break
        if fill.action in ("open", "increase"):
            openings.append(fill)
    return openings


def argentina_ratio_credit_ok(
    state: BaseEngine, rules: "ArgentinaEquityEngine", symbol: str, today: date,
) -> bool:
    """Block sales of certificates not yet credited after a ratio change.

    From the ex-date the position is valued in post-change units, but Caja de
    Valores credits the additional certificates only on ``credit_date``. A
    position opened before the ex-date may be sold only by a trade that
    settles on/after that credit. BaseEngine does not pass the order size, so
    this conservatively blocks the whole position, not just the extra units.
    """
    openings = _lifecycle_openings(state, symbol)
    first = min((_trade_date(f.timestamp) for f in openings), default=None)
    if first is None:
        return True
    for event in rules.ratio_adjustments:
        if event["symbol"].upper() != symbol.upper():
            continue
        ex = date.fromisoformat(event["effective_date"])
        if not first < ex <= today:
            continue
        credit = date.fromisoformat(event["credit_date"])
        try:
            sessions = getattr(rules, "credit_check_sessions", None) or rules.settlement_sessions
            if settlement_date(today, sessions) < credit:
                return False
        except (TypeError, ValueError):
            return False
    return True


def argentina_sell_allowed(
    state: BaseEngine, rules: "ArgentinaEquityEngine", symbol: str, today: date | None,
) -> bool:
    """Settled securities and credited certificates: the BYMA sell rule."""
    sessions = rules.settlement_sessions
    if today is None or sessions is None:
        return False
    return (argentina_sell_is_settled(state, symbol, today, sessions)
            and argentina_ratio_credit_ok(state, rules, symbol, today))


def argentina_available_cash(
    state: BaseEngine, rules: "ArgentinaEquityEngine", timestamp,
) -> float:
    """Buying power for a purchase at *timestamp*, from *state*'s ledger."""
    if rules.settlement_sessions is None:
        raise ValueError("BYMA buying power requires a verified settlement calendar")
    today = _trade_date(timestamp)
    if today is None:
        raise ValueError("BYMA buying power requires a session timestamp")
    purchase_due = settlement_date(today, rules.settlement_sessions)
    return available_for_settlement(
        state.capital, state.fill_records, today, purchase_due,
        rules.settlement_sessions,
    )


def argentina_reduction_buying_power(
    state: BaseEngine, rules: "ArgentinaEquityEngine", reductions: list, timestamp,
) -> float:
    """Same-bar sales fund same-bar purchases: equal normal settlement dates.

    Assumes one validated currency/clearing pool, not instant cash or a
    permission to withdraw before settlement.
    """
    if rules.settlement_sessions is None:
        raise ValueError("BYMA netting requires a verified settlement calendar")
    today = _trade_date(timestamp)
    if today is None:
        raise ValueError("BYMA netting requires a valid session date")
    settlement_date(today, rules.settlement_sessions)
    return sum(order.capital_credit for order in reductions)


def argentina_diagnostics(state: BaseEngine, rules: "ArgentinaEquityEngine") -> dict:
    """Run facts: price basis, ratio handling and terminal equity contents."""
    if not state.equity_snapshots:
        return {}
    last_day = _trade_date(state.equity_snapshots[-1].timestamp)
    sessions = rules.settlement_sessions
    if last_day is None or sessions is None:
        return {}
    receivable = pending_sale_proceeds(state.fill_records, last_day, sessions)
    retained = state.unliquidated_positions
    scheduled = [s for s, i in rules.instruments.items() if i.get("tick_size") is None]
    early_fills = sum(
        1 for f in state.fill_records
        if f.symbol.upper() in scheduled and _trade_date(f.timestamp) < TICK_SCHEDULE_EFFECTIVE
    )
    fees = [(f.signed_quantity, float(f.fee)) for f in state.fill_records]
    final_equity = state.equity_snapshots[-1].equity
    return {
        "ar_net_pnl": round(final_equity - state.initial_capital, 6),
        "ar_data_basis": _data_basis_summary(rules, scheduled, early_fills),
        # The run's single cash ledger currency (loader-declared, validated
        # against instrument metadata), so its amounts can be cited with it.
        "settlement_currency": rules.currency,
        "ar_total_fees": round(sum(fee for _, fee in fees), 6),
        "ar_buy_fees": round(sum(fee for qty, fee in fees if qty > 0), 6),
        "ar_sell_fees": round(sum(fee for qty, fee in fees if qty < 0), 6),
        "ar_fill_count": len(fees),
        "ar_price_basis": "source_split_dividend_adjusted_ohlc_raw_volume",
        "ar_metadata_provenance": dict(rules.metadata_provenance),
        "ar_calendar_provenance": dict(rules.calendar_provenance),
        "ar_fee_basis": dict(rules.fee_basis),
        "ar_tick_schedule": {
            "source": TICK_SCHEDULE_ID, "official_from": str(TICK_SCHEDULE_EFFECTIVE),
            "symbols": scheduled, "fills_before_official_version": early_fills,
        } if scheduled else {"source": "declared tick_size"},
        "ar_cedear_ratio_adjustments": list(rules.ratio_adjustments),
        "ar_dropped_non_session_bars": dict(rules.dropped_non_session_bars),
        "ar_unsettled_sale_receivable_at_end": round(receivable, 6),
        "ar_unliquidated_position_count": len(retained),
        "ar_unliquidated_positions": [
            {k: (round(v, 6) if isinstance(v, float) else v) for k, v in item.items()}
            for item in retained
        ],
        "ar_terminal_equity_basis": (
            "unsettled_positions_marked_net_of_estimated_exit_costs"
            if retained
            else "all_positions_liquidated_at_last_close"
        ),
    }


def argentina_can_execute(
    state: BaseEngine, rules: ArgentinaEquityEngine,
    symbol: str, direction: int, bar: pd.Series,
) -> bool:
    """State/rules helper shared by standalone and composite routing."""
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
    return argentina_sell_allowed(state, rules, symbol, _trade_date(bar.name))
