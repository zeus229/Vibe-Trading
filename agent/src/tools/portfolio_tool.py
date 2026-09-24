"""Read-only, sanitized portfolio context for model-assisted analysis."""

from __future__ import annotations

import json
from typing import Any

from src.agent.tools import BaseTool
from src.portfolio.service import PortfolioService


#: Small, portfolio-level fields, emitted first. ``daily_change`` is the
#: aggregate daily price move (pct/coverage_pct/source/method) computed by
#: Asistente Casa and forwarded verbatim through the connector; it must never
#: be recomputed here by summing per-position figures. ``daily_contributors``
#: is precomputed here (see ``_build_daily_contributors``) from the same
#: fields, over every position, before any truncation narrows the view.
_SUMMARY_FIELDS_FIRST: tuple[str, ...] = (
    "as_of",
    "complete",
    "totals",
    "daily_change",
    "daily_report",
    "daily_contributors",
    "canonical_positions",
    "risk_xray_args",
    "warnings",
    "privacy",
)

_TOP_CONTRIBUTOR_COUNT = 8
_TOP_MOVER_COUNT = 5


def _build_daily_report(
    context: dict[str, Any],
    daily_contributors: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build a compact daily portfolio view that fits before tool truncation.

    The report preserves only the canonical fields needed by scheduled daily
    summaries. It never recalculates type, weights or daily changes. Contributor
    rows are copied from the deterministic precomputation and capped to the top
    three on each side so the 37-position report remains visible inside the
    model-facing tool-result limit.
    """
    positions = context.get("canonical_positions")
    daily_change = context.get("daily_change")
    if not isinstance(positions, list) or not isinstance(daily_change, dict):
        return None

    rows = []
    for row in positions:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "symbol": row.get("symbol"),
                "instrument_type": row.get("instrument_type"),
                "weight_total_portfolio": row.get("weight_total_portfolio"),
                "daily_change_pct": row.get("daily_change_pct"),
                "daily_change_as_of": row.get("daily_change_as_of"),
                "daily_change_status": row.get("daily_change_status"),
            }
        )

    contributors = daily_contributors if isinstance(daily_contributors, dict) else {}
    return {
        "units": {
            "weight_total_portfolio": "fraction",
            "daily_change_pct": "percent",
            "contribution_pp": "percentage_points",
        },
        "daily_change": daily_change,
        "position_count": len(rows),
        "positions": rows,
        "top_positive_contributors": list(
            contributors.get("top_positive_contributors") or []
        )[:3],
        "top_negative_contributors": list(
            contributors.get("top_negative_contributors") or []
        )[:3],
    }


def _build_daily_contributors(context: dict[str, Any]) -> dict[str, Any] | None:
    """Precompute which positions explain today's portfolio move.

    Uses exactly the same per-position ``market_value_native`` and
    ``daily_change_pct`` that already feed the top-level ``daily_change``
    aggregate (see ``vibe_portfolio.py`` on the Asistente Casa side) — no
    price or daily-change recomputation, no extra PPI/Yahoo call. Computed
    over every position in ``holdings_native`` (all currencies, all 40+
    holdings) before the result is truncated for delivery, so the ranking is
    never biased toward whichever positions happen to survive the cut.

    ``contribution_pp`` renormalizes the weight over only the positions with
    ``daily_change_status == "ready"`` (not the whole portfolio), so
    ``sum(contribution_pp)`` reproduces ``daily_change.pct`` even when
    coverage is below 100% — not only when every position happens to have a
    valid figure.

    Returns ``None`` when there is nothing to rank (no covered positions).
    """
    holdings_native = context.get("holdings_native")
    if not isinstance(holdings_native, dict):
        return None

    covered: list[dict[str, Any]] = []
    for rows in holdings_native.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("daily_change_status") != "ready":
                continue
            pct = row.get("daily_change_pct")
            value = row.get("market_value_native")
            if not isinstance(pct, (int, float)) or not isinstance(value, (int, float)):
                continue
            covered.append({"symbol": row.get("symbol"), "pct": float(pct), "value": float(value)})

    total_covered_value = sum(row["value"] for row in covered)
    if total_covered_value <= 0:
        return None

    contributors = [
        {
            "symbol": row["symbol"],
            "daily_change_pct": row["pct"],
            "weight_pct": round(row["value"] / total_covered_value * 100.0, 2),
            "contribution_pp": round(row["value"] / total_covered_value * row["pct"], 2),
        }
        for row in covered
    ]

    by_contribution = sorted(contributors, key=lambda c: c["contribution_pp"], reverse=True)
    by_change = sorted(contributors, key=lambda c: c["daily_change_pct"], reverse=True)

    return {
        "as_of": (context.get("daily_change") or {}).get("as_of"),
        "method": "weight_fraction_times_daily_change_pct",
        "covered_position_count": len(contributors),
        "top_positive_contributors": by_contribution[:_TOP_CONTRIBUTOR_COUNT],
        "top_negative_contributors": list(reversed(by_contribution[-_TOP_CONTRIBUTOR_COUNT:])),
        "top_movers_up": by_change[:_TOP_MOVER_COUNT],
        "top_movers_down": list(reversed(by_change[-_TOP_MOVER_COUNT:])),
    }


def _reorder_for_truncation_safety(context: dict[str, Any]) -> dict[str, Any]:
    """Put small aggregate fields before the large per-position payloads.

    ``truncate_tool_result`` (``src/config/limits.py``) cuts any oversized
    tool result at a flat character offset with no knowledge of JSON
    structure. A 40-position portfolio's ``holdings``/``holdings_native``
    alone routinely exceed that limit on their own, so whatever key ordering
    this dict uses decides what a truncated caller actually receives. Moving
    ``account_allocation``/``holdings``/``holdings_native`` (large, one entry
    per position) to the end means a truncation drops positions first and
    never the portfolio-level aggregates. This only reorders keys — every
    field ``PortfolioService.analysis_context()`` returns is still present
    and unchanged when the result is not truncated.
    """
    ordered: dict[str, Any] = {}
    for key in _SUMMARY_FIELDS_FIRST:
        if key in context:
            ordered[key] = context[key]
    for key, value in context.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


class PortfolioSummaryTool(BaseTool):
    """Expose the latest local portfolio snapshot as sanitized analysis context."""

    name = "portfolio_summary"
    description = (
        "Read the latest sanitized snapshot of the user's locally configured "
        "read-only brokerage accounts. Returns deterministic totals, "
        "daily_change (the portfolio's aggregate daily price move: pct, "
        "coverage_pct, source, method — use this directly, verbatim, for "
        "'how much did my portfolio move today' questions; never recompute "
        "it by summing per-position figures), daily_contributors (already "
        "precomputed over every position, before any truncation — use "
        "top_positive_contributors/top_negative_contributors directly, "
        "verbatim, for 'which positions explain today's move' questions; "
        "daily_report is the preferred compact view for scheduled daily "
        "reports: it includes all positions with canonical instrument_type, "
        "weight_total_portfolio and daily_change fields plus top-3 positive "
        "and negative contributors before truncation. daily_report.units is "
        "authoritative for rendering: daily_change_pct is percent, "
        "contribution_pp is percentage points, and weight_total_portfolio is "
        "a fraction. Never print daily_change_pct as a unitless number or "
        "contribution_pp without pp; preserve the same unit in the figures "
        "declaration. canonical_positions "
        "remains the authoritative full per-position view; use its fields "
        "verbatim rather than reconstructing them from combined holdings; "
        "never call calc to derive contribution — a value calc produces is "
        "not traceable evidence and will be redacted from the answer). For "
        "'which positions moved most/least today' by pure percentage change "
        "(not contribution), use daily_contributors.top_movers_up/"
        "top_movers_down — that is a different ranking from contribution, "
        "since a large position with a small move can contribute more than "
        "a small position with a big move. Also returns account allocation, "
        "combined holdings, weights, unrealized P/L, data-quality warnings, "
        "and risk_xray_args (symbols + weights) that can be passed straight "
        "to the portfolio_risk_xray tool. A source that failed to refresh is "
        "reported as an error and excluded from the totals, so a snapshot "
        "with complete=false is missing accounts. It never returns "
        "credentials, account numbers, order IDs, personal names, or local "
        "paths. Use the Web Portfolio refresh button before requesting current "
        "data."
    )
    parameters = {"type": "object", "properties": {}, "required": []}
    repeatable = True
    is_readonly = True

    def execute(self, **_: Any) -> str:
        """Return the sanitized portfolio context as a JSON envelope.

        Returns:
            A JSON string with ``status`` ``ok`` and the context, or ``empty``
            with a hint when no usable snapshot exists. The context's fields
            are reordered (never dropped or changed) so that a truncated
            result still carries the portfolio-level aggregates.
        """
        context = PortfolioService().analysis_context()
        if context is None:
            return json.dumps(
                {
                    "status": "empty",
                    "message": "No portfolio snapshot exists. Refresh the Portfolio page first.",
                },
                ensure_ascii=False,
            )
        daily_contributors = _build_daily_contributors(context)
        daily_report = _build_daily_report(context, daily_contributors)
        additions: dict[str, Any] = {}
        if daily_report is not None:
            additions["daily_report"] = daily_report
        if daily_contributors is not None:
            additions["daily_contributors"] = daily_contributors
        if additions:
            context = {**context, **additions}
        return json.dumps(
            {"status": "ok", "context": _reorder_for_truncation_safety(context)},
            ensure_ascii=False,
        )
