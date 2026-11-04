"""Agent tool for validated, versioned structured financial research."""

from __future__ import annotations

from typing import Any

from src.research_artifacts.financial_facts import encode_financial_facts
from src.research_artifacts.store import ResearchArtifactStore
from src.tools.research_artifact_tool import _error, _ok
from src.agent.tools import BaseTool


class SaveFinancialResearchArtifactTool(BaseTool):
    """Validate a financial facts object before creating an immutable revision."""

    name = "save_financial_research_artifact"
    description = (
        "Save issuer financial statement facts in the opt-in financial-facts/v1 format "
        "as an immutable versioned research artifact. Pass document as a JSON object "
        "with schema_version, subject, sources, and facts; do not pass a JSON string. "
        "Facts must identify period (duration start/end or instant as-of), metric, "
        "accounting scope/definition, numeric value or null, currency, units, source "
        "ID/section, and per-fact verification status. Include documented formulas and "
        "operands for derived values when available. Use partial or unavailable when "
        "evidence is insufficient. A verified label asserts cited evidence; it is NOT "
        "independent audit or source verification. On a correction, provide artifact_id "
        "to append a linked revision. Narrative research uses save_research_artifact."
    )
    is_readonly = False
    repeatable = True
    parameters = {
        "type": "object",
        "properties": {
            "document": {
                "type": "object",
                "description": "Complete financial-facts/v1 envelope, including source catalog and facts",
            },
            "data_as_of": {"type": "string", "description": "Date or timestamp of the researched data"},
            "artifact_id": {"type": "string", "description": "Existing artifact series to revise"},
            "universe": {"type": "string", "description": "Optional market or asset universe"},
            "evidence": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Optional artifact-level references; do not confuse with fact-level sources",
            },
            "verification_status": {
                "type": "string",
                "description": "Artifact revision status; independent of fact-level statuses, defaults to unverified",
            },
            "completeness": {"type": "string", "description": "Revision completeness state"},
            "hypothesis_id": {"type": "string"},
            "goal_id": {"type": "string"},
            "session_id": {"type": "string"},
            "run_id": {"type": "string"},
        },
        "required": ["document", "data_as_of"],
        "additionalProperties": False,
    }

    def execute(self, **kwargs: Any) -> str:
        try:
            document = kwargs["document"]
            content = encode_financial_facts(document)
            subject = document["subject"].strip()  # Normalized/validated by encoder
            store = ResearchArtifactStore()
            try:
                result = store.save(
                    artifact_id=kwargs.get("artifact_id"),
                    subject=subject,
                    universe=kwargs.get("universe"),
                    content=content,
                    data_as_of=kwargs["data_as_of"],
                    evidence=kwargs.get("evidence"),
                    verification_status=kwargs.get("verification_status"),
                    completeness=kwargs.get("completeness"),
                    hypothesis_id=kwargs.get("hypothesis_id"),
                    goal_id=kwargs.get("goal_id"),
                    session_id=kwargs.get("session_id"),
                    run_id=kwargs.get("run_id"),
                )
                return _ok(result.to_dict())
            finally:
                store.close()
        except (KeyError, TypeError, ValueError) as exc:
            return _error(exc)
        except Exception as exc:
            return _error(exc)
