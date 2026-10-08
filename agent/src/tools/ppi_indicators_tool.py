"""Calculate basic indicators from validated Asistente Casa PPI MCP OHLCV.

This is not a market-data loader and never falls back to Yahoo/underlyings.
Input bars are loaded from the exact consultar_mercado_ppi_instrumento tool
result in the current session trace, never reconstructed by the model.
"""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

import pandas as pd

from src.agent.tools import BaseTool
from src.config.paths import get_sessions_dir
from src.tools.technical_indicator_tool import (
    _compute_macd, _compute_rsi, _compute_sma,
)

_CONTRACT = "asistente_casa_ppi_market_data_v1"


class CalculatePPIIndicatorsTool(BaseTool):
    name = "calculate_ppi_indicators"
    description = (
        "Calculate SMA20, SMA50, RSI14 and MACD on the exact daily OHLCV "
        "bars returned by Asistente Casa MCP consultar_mercado_ppi_instrumento "
        "in this session (not Yahoo or the US underlying). The tool reads the "
        "longest observed history for the unique PPI instrument in this run. "
        "If several instruments were queried, supply the exact requested BYMA "
        "symbol and instrument_type to select one; never supply or invent bars. "
        "Only for interactive research; not backtesting or canonical portfolio performance."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Exact requested BYMA symbol, e.g. XLV.BA; needed if multiple instruments were queried",
            },
            "instrument_type": {"type": "string", "description": "PPI instrument type, e.g. CEDEARS"},
        },
        "required": [],
    }
    repeatable = True
    is_readonly = True
    replay_after_compaction = False

    def __init__(self, default_session_id: str | None = None, event_callback: Any = None) -> None:
        self.session_id = default_session_id

    def _verified_payload(self, symbol: str | None, instrument_type: str | None,
                          calculation_call_id: str) -> tuple[dict[str, Any], str] | None:
        """Select the longest exact BYMA history observed in this run."""
        if not self.session_id:
            return None
        trace = get_sessions_dir() / self.session_id / "trace.jsonl"
        try:
            rows = [json.loads(line) for line in trace.read_text().splitlines() if line.strip()]
        except (OSError, ValueError):
            return None
        last_start = max((i for i, row in enumerate(rows) if row.get("type") == "start"), default=-1)
        if last_start < 0:
            return None
        if not any(row.get("type") == "tool_call"
                   and row.get("tool") == self.name
                   and row.get("call_id") == calculation_call_id
                   for row in rows[last_start + 1:]):
            return None
        candidates: list[tuple[dict[str, Any], str]] = []
        for row in rows[last_start + 1:]:
            if (row.get("type") != "tool_result"
                    or row.get("tool") != "mcp_asistente_casa_consultar_mercado_ppi_instrumento"
                    or row.get("status") != "ok"):
                continue
            observed = row.get("result")
            if isinstance(observed, str):
                try:
                    observed = json.loads(observed)
                except ValueError:
                    return None
            if not isinstance(observed, dict) or observed.get("status") not in ("ok", "success", "available"):
                continue
            data = observed.get("data")
            if not isinstance(data, dict) or not isinstance(row.get("call_id"), str):
                continue
            if symbol and data.get("requested_symbol") != symbol:
                continue
            if instrument_type and data.get("instrument_type") != instrument_type:
                continue
            candidates.append((data, row["call_id"]))
        identities = {(data.get("requested_symbol"), data.get("instrument_type"), data.get("market"))
                      for data, _ in candidates}
        if len(identities) != 1:
            return None
        return max(candidates, key=lambda item: len(item[0].get("bars", []))
                   if isinstance(item[0].get("bars"), list) else -1)

    def execute(self, **kwargs: Any) -> str:
        symbol = kwargs.get("symbol")
        instrument_type = kwargs.get("instrument_type")
        calculation_call_id = kwargs.get("_runtime_call_id")
        if not isinstance(calculation_call_id, str) or not calculation_call_id.startswith("call_"):
            return json.dumps({"ok": False, "error": "runtime calculation call ID required"})
        if (symbol is not None and not isinstance(symbol, str)) or (
                instrument_type is not None and not isinstance(instrument_type, str)):
            return json.dumps({"ok": False, "error": "invalid PPI selector"})
        observed = self._verified_payload(symbol, instrument_type, calculation_call_id)
        if observed is None:
            return json.dumps({"ok": False, "error": "no unique PPI instrument observed in this session"})
        payload, call_id = observed
        if (
            payload.get("ok") is not True
            or payload.get("contract_version") != _CONTRACT
            or payload.get("source") != "ppi_marketdata"
            or payload.get("market") != "BYMA"
            or payload.get("indicator_eligible") is not True
            or payload.get("history_status") != "sufficient"
            or payload.get("quality_issues") != []
            or payload.get("currency") not in ("ARS", "USD")
            or payload.get("currency_source") != "ppi_instrument_catalog"
        ):
            return json.dumps({"ok": False, "error": "PPI identity/history quality not verified"})
        bars = payload.get("bars")
        if not isinstance(bars, list) or not 30 <= len(bars) <= 730:
            return json.dumps({"ok": False, "error": "insufficient or oversized daily history"})
        dates: list[date] = []
        closes: list[float] = []
        for bar in bars:
            if not isinstance(bar, dict):
                return json.dumps({"ok": False, "error": "malformed PPI bar"})
            try:
                parsed_date = date.fromisoformat(bar["date"])
                close = float(bar["close"])
                opening = float(bar["open"])
                high = float(bar["high"])
                low = float(bar["low"])
                volume = float(bar["volume"])
            except (KeyError, TypeError, ValueError, OverflowError):
                return json.dumps({"ok": False, "error": "incomplete PPI OHLCV"})
            if any(isinstance(bar[k], bool) for k in ("close", "open", "high", "low", "volume")):
                return json.dumps({"ok": False, "error": "boolean PPI OHLCV"})
            if (
                not all(math.isfinite(x) for x in (close, opening, high, low, volume))
                or close <= 0 or volume < 0
                or not low <= min(opening, close) <= max(opening, close) <= high
                or (dates and parsed_date <= dates[-1])
            ):
                return json.dumps({"ok": False, "error": "invalid or unordered PPI OHLCV"})
            dates.append(parsed_date)
            closes.append(close)
        if payload.get("observation_count") != len(bars):
            return json.dumps({"ok": False, "error": "PPI observation count mismatch"})
        series = pd.Series(closes, index=pd.DatetimeIndex(dates), dtype=float)
        indicators: dict[str, Any] = {
            "sma_20": _compute_sma(series, 20),
            "sma_50": _compute_sma(series, 50),
            "sma_200": _compute_sma(series, 200),
            "rsi_14": _compute_rsi(series),
            "macd": _compute_macd(series),
        }
        return json.dumps({
            "ok": True,
            "source": "ppi_marketdata",
            "source_call_id": call_id,
            "calculation_call_id": calculation_call_id,
            "symbol": payload.get("requested_symbol"),
            "ppi_symbol": payload.get("symbol"),
            "instrument_type": payload.get("instrument_type"),
            "market": "BYMA",
            "currency": payload["currency"],
            "currency_label": payload.get("currency_label"),
            "bar_count": len(bars),
            "first_date": dates[0].isoformat(),
            "last_date": dates[-1].isoformat(),
            "latest_close": closes[-1],
            "indicators": indicators,
            "calculation": "deterministic_from_supplied_ppi_bars",
            "warning": "PPI OHLCV not certified for corporate actions or backtesting",
        }, ensure_ascii=False, allow_nan=False)


__all__ = ["CalculatePPIIndicatorsTool"]
