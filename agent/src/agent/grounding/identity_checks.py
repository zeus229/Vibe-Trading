"""Declared identity checks for the grounding gate (#1622).

First checks migrated off the inline rule list in ``policies.py``; the
predicates are byte-for-byte the logic that used to run inline, now declared
with a stable name so run cards can say what fired.
"""

from __future__ import annotations

import re
from typing import Any

from src.agent.grounding.registry import grounding_check

#: An answer that relabels a locked listed identity as private contradicts the
#: resolver, which is an identity finding rather than a figure finding.
_PRIVATE_ASSERTION_RE = re.compile(
    r"(?:\b(?:is|remains|still)\s+(?:an?\s+)?(?:private company|privately held)\b|"
    r"\bnot publicly traded\b|\bunlisted company\b|"
    r"(?:是|仍是|属于)(?:一家)?(?:私人|私营|非上市)公司|未上市|没有上市)",
    re.IGNORECASE,
)


@grounding_check(
    name="listed-identity-relabelled-private", phase="identity", order=1,
    code="listed_identity_relabelled_private",
    description=(
        "A locked listed identity (security or fund) was relabelled as "
        "private or unlisted in the answer without a conflicting resolver result."
    ),
)
def _listed_identity_relabelled_private(ledger: Any, content: str) -> list[dict[str, Any]]:
    listed = [
        record
        for record in ledger._identities.values()
        if record.status == "locked" and record.instrument_type in {"listed_security", "fund"}
    ]
    if not listed or not _PRIVATE_ASSERTION_RE.search(content):
        return []
    symbols = sorted(record.symbol for record in listed if record.symbol)
    return [
        {
            "code": "listed_identity_relabelled_private",
            "symbols": symbols,
            "value": None,
            "role": None,
            "span": None,
            "symbol": None,
            "reason": "listed_relabelled_private",
            "message": (
                f"Locked listed identity {', '.join(symbols)} was relabelled as "
                "private/unlisted without a conflicting resolver result."
            ),
        }
    ]


@grounding_check(
    name="identity-not-locked", phase="identity", order=0,
    code="identity_not_locked",
    description=(
        "A run that named an instrument concluded while its identity stayed unresolved, conflicting, or invalidated."
    ),
)
def _identity_not_locked(ledger: Any, content: str) -> list[dict[str, Any]]:
    # Only a run that named an instrument can get its identity wrong: the
    # trigger phrase matches the user message, so "什么是市盈率估值法？" would
    # otherwise fail every draft. ``ambiguous`` is absent on purpose: a
    # shortlist is an answer and consumers stay blocked in ``authorize_tool_call``.
    status = ledger.identity_status
    if not (
        ledger._identity_required and ledger._identities and status in {"unresolved", "conflicting", "invalidated"}
    ):
        return []
    return [
        {
            "code": "identity_not_locked",
            "status": status,
            "value": None,
            "role": None,
            "span": None,
            "symbol": None,
            "reason": "identity_not_locked",
            "message": (f"Instrument identity is {status}; a final market conclusion requires locked identity."),
        }
    ]


@grounding_check(
    name="portfolio-identity-conflict", phase="identity", order=0,
    code="portfolio_identity_conflict",
    description="Do not make market claims about symbols with contradictory verified ISIN mappings.",
)
def _portfolio_identity_conflict(ledger: Any, content: str) -> list[dict[str, Any]]:
    blocked = ledger._portfolio_conflict_symbols()
    # A standalone explanation of an unavailable identity is not a market
    # conclusion. Keep this exemption narrow: a second sentence or a claim
    # following the refusal must still be checked against the symbols.
    refusal = re.fullmatch(
        r"\s*(?:No puedo analizar|No se puede analizar|Cannot analyze|Unable to analyze)"
        r"\s+([A-Za-z0-9_.]+)\s+(?:porque|because)\s+"
        r"(?:su identidad es contradictoria|hay un conflicto de identidad|"
        r"its identity is conflicting|its identity is unresolved)"
        r"\.?(?:\s*)",
        content, flags=re.IGNORECASE,
    )
    if refusal and any(
        refusal.group(1).upper() in {symbol, symbol.rsplit(".", 1)[0]}
        for symbol in blocked
    ):
        return []
    # Unlike an upstream resolver shortlist, these observations contradict
    # identity data. Only claims explicitly naming an affected security are
    # blocked; unrelated holdings and generic portfolio prose remain usable.
    for symbol in sorted(blocked):
        root = symbol.rsplit(".", 1)[0]
        # Only use a bare root when unambiguous among the affected listings.
        roots = {item.rsplit(".", 1)[0] for item in blocked}
        names = [symbol]
        if sum(item.rsplit(".", 1)[0] == root for item in blocked) == 1:
            names.append(root)
        for name in names:
            match = re.search(
                r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"(?![A-Za-z0-9_.])",
                content, flags=re.IGNORECASE,
            )
            if match:
                return [{
                    "code": "portfolio_identity_conflict",
                    "reason": "portfolio_identity_conflict",
                    "symbol": symbol,
                    "value": None,
                    "role": None,
                    "span": [match.start(), match.end()],
                    "message": "Portfolio evidence contradicts the ISIN for this symbol; a ticker-only resolver result cannot settle it.",
                }]
    return []
