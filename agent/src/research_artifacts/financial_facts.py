"""Portable, versioned financial facts inside existing research artifact content.

No database migration: the envelope is JSON serialized in the existing content field.
Validation checks shape and provenance, NOT whether a reported number is true.
"""

from __future__ import annotations

import json
import math
from typing import Any

SCHEMA_VERSION = "financial-facts/v1"
STATUSES = {"verified", "partial", "unavailable"}
KINDS = {"duration", "instant"}
MAX_FACTS = 1000


def validate_financial_facts(document: object) -> dict[str, Any]:
    """Validate a financial-facts envelope and return its normalized shape.

    Raises ValueError on malformed or ambiguous input. No inference, currency
    conversion, annualization, or verification of the underlying evidence.
    """
    if not isinstance(document, dict):
        raise ValueError("financial facts document must be an object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported financial facts schema_version")
    subject = document.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("subject must be a non-empty string")
    facts = document.get("facts")
    if not isinstance(facts, list) or len(facts) > MAX_FACTS:
        raise ValueError("facts must be a list of at most 1000 records")
    normalized: list[dict[str, Any]] = []
    for index, fact in enumerate(facts):
        prefix = f"facts[{index}]"
        if not isinstance(fact, dict):
            raise ValueError(f"{prefix} must be an object")
        required = ("metric", "period", "period_kind", "scope", "definition",
                    "verification_status", "value", "currency", "unit",
                    "source_url", "source_section")
        missing = [key for key in required if key not in fact]
        if missing:
            raise ValueError(f"{prefix} missing fields: {', '.join(missing)}")
        for key in ("metric", "period", "scope", "definition"):
            if not isinstance(fact[key], str) or not fact[key].strip():
                raise ValueError(f"{prefix}.{key} must be non-empty text")
        if fact["period_kind"] not in KINDS:
            raise ValueError(f"{prefix}.period_kind must be duration or instant")
        status = fact["verification_status"]
        if status not in STATUSES:
            raise ValueError(f"{prefix}.verification_status is invalid")
        value = fact["value"]
        if value is not None and (isinstance(value, bool) or
                                  not isinstance(value, (int, float)) or
                                  not math.isfinite(value)):
            raise ValueError(f"{prefix}.value must be finite numeric or null")
        if status == "unavailable" and value is not None:
            raise ValueError(f"{prefix}: unavailable facts must have null value")
        if status == "verified" and value is None:
            raise ValueError(f"{prefix}: verified facts require a numeric value")
        for key in ("currency", "unit", "source_url", "source_section"):
            item = fact[key]
            if item is not None and (not isinstance(item, str) or not item.strip()):
                raise ValueError(f"{prefix}.{key} must be non-empty text or null")
        if status == "verified" and (not fact["source_url"] or
                                     not fact["source_section"] or
                                     not fact["currency"] or not fact["unit"]):
            raise ValueError(f"{prefix}: verified facts require provenance and units")
        if fact.get("source_page") is not None and (
            isinstance(fact["source_page"], bool) or
            not isinstance(fact["source_page"], (str, int))
        ):
            raise ValueError(f"{prefix}.source_page must be string, integer or null")
        normalized.append(dict(fact))
    return {"schema_version": SCHEMA_VERSION, "subject": subject.strip(),
            "facts": normalized}


def encode_financial_facts(document: object) -> str:
    """Serialize a validated envelope for ResearchArtifactStore.save(content=...)."""
    return json.dumps(validate_financial_facts(document), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)


def decode_financial_facts(content: str) -> dict[str, Any]:
    """Read an envelope from a research revision; reject legacy narrative."""
    try:
        document = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValueError("research content is not a financial facts JSON envelope") from exc
    return validate_financial_facts(document)
