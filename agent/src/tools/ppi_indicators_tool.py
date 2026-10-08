"""Calculate basic indicators from validated Asistente Casa PPI MCP OHLCV.

This is not a market-data loader and never falls back to Yahoo/underlyings.
Input bars must be the response of consultar_mercado_ppi_instrumento. Output
describes calculations on provided bars, not independent source verification.
"""
from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

import pandas as pd

from src.agent.tools import BaseTool
from src.tools.technical_indicator_tool import (
    _compute_macd, _compute_rsi, _compute_sma,
)

_CONTRACT = "asistente_casa_ppi_market_data_v1"


class CalculatePPIIndicatorsTool(BaseTool):
    name = "calculate_ppi_indicators"
    description = (
        "Calculate SMA20, SMA50, RSI14 and MACD on the exact consecutive "
        "daily OHLCV bars returned by Asistente Casa MCP "
        "consultar_mercado_ppi_instrumento (not Yahoo or the US underlying). "
        "Supply the full verified MCP response as payload, including "
        "source, market, currency, quality and bar dates. Only for interactive "
        "research; not backtesting or canonical portfolio performance."
    )
    parameters = {
        "type": "object",
        "properties": {
            "payload": {
                "type": "object",
                "description": "Complete JSON output of consultar_mercado_ppi_instrumento",
            },
            "source_call_id": {
                "type": "string",
                "description": "Exact MCP tool call ID that supplied the bars for citations",
            },
        },
        "required": ["payload", "source_call_id"],
    }
    repeatable = True
    is_readonly = True
    replay_after_compaction = False

    def execute(self, **kwargs: Any) -> str:
        payload = kwargs.get("payload")
        call_id = kwargs.get("source_call_id")
        if not isinstance(payload, dict) or not isinstance(call_id, str) or not call_id.startswith("call_"):
            return json.dumps({"ok": False, "error": "complete MCP payload and source call ID required"})
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
            "symbol": payload.get("symbol"),
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
