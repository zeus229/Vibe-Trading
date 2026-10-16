"""Bounded contract recovery when a correction still has grounding issues."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agent.grounding.policies import ValidationResult
from src.agent.grounding.repair_contract import CorrectionContract
from src.agent.loop_grounding import validate_and_release


@pytest.fixture(params=["benchmark", "contribution", "aggregate"])
def scenario(request):
    """Synthetic observed claims for the three incident-shaped pathways."""
    ref = "call_synthetic::data.rows[0].metric"
    issue = {
        "code": "numeric_claim_conflict" if request.param == "benchmark" else "figure_undeclared",
        "reason": "entity_ref_needs_scoped_claim" if request.param == "benchmark" else "figure_undeclared",
        "value": "12.5",
        "figure_value": 12.5,
        "figure_percent": False,
        "figure_digits": "12.5",
        "exact_ref_repair_candidate": ref,
        "field_ref_candidates": [ref],
    }
    if request.param == "benchmark":
        issue["entity_ref_symbol"] = "SYNTH_A"
    if request.param == "aggregate":
        issue["aggregate_ref_candidate"] = ref
        issue["exact_ref_repair_candidate"] = None
        issue["field_ref_candidates"] = []
    return request.param, issue, ref


class _Trace:
    def __init__(self):
        self.entries = []

    def write(self, entry):
        self.entries.append(entry)

    def write_text_entry(self, entry, **kwargs):
        self.entries.append(entry)


class _Grounding:
    def __init__(self, issue, ref, final_valid):
        self.issue = issue
        self.ref = ref
        self.final_valid = final_valid
        self.redacted = False
        self.validation_count = 2
        self.figures_removed = 0
        self.should_buffer_output = False

    def validate_final_answer(self, content):
        if self.final_valid and content.startswith("restored:"):
            return ValidationResult(True, [], released_text=content)
        return ValidationResult(False, [self.issue], released_text="")

    revalidate = validate_final_answer

    def repair_undeclared_attribution(self, content, validation):
        return None

    def repair_provenance(self, content, validation):
        return None

    def correction_contract(self, validation):
        return CorrectionContract.from_validation(validation)

    @staticmethod
    def correction_contract_missing(content, contract, validation=None):
        return contract.missing_figures(content, validation=validation)

    def correction_prompt(self, validation):
        return "[GROUNDING GATE] fix remaining synthetic issue"

    def recovery_action(self, validation):
        return None

    def redacted_release(self, content, validation):
        self.redacted = True
        return "analysis (omitted※)"

    def safe_fallback(self):
        return "unavailable"

    def pending_recovery_stub(self, content):
        return None


def _run(grounding, content, *, revisions=0, contract=None, contract_retries=0,
         iteration=1, max_revisions=2, messages=None):
    emitted = []
    loop = SimpleNamespace(
        _grounding=grounding,
        max_iterations=8,
        _released_fallback=False,
        _released_fallback_reason="",
        _emit=lambda kind, payload: emitted.append((kind, payload)),
    )
    return validate_and_release(
        loop,
        final_content=content,
        grounding_revisions=revisions,
        grounding_correction_text_only=True,
        pending_grounding_draft="previous rejected draft",
        pending_grounding_contract=contract,
        correction_contract_retries=contract_retries,
        forced_grounding_release=False,
        syntax_fallback_emitted=False,
        stream=SimpleNamespace(buffer_text_output=False, streamed_chars=0, thinking_chunks=[]),
        trace=_Trace(),
        react_trace=[],
        messages=messages if messages is not None else [],
        iteration=iteration,
        current_iter=iteration,
        max_revisions=max_revisions,
    )


def _declared(value="12.5", ref="call_synthetic::data.rows[0].metric"):
    return (
        "restored: metric " + value + "\n\n```figures\n"
        + value + " | observed | synthetic metric | " + ref + "\n```"
    )


def test_invalid_correction_gets_one_bounded_contract_retry(scenario):
    name, issue, ref = scenario
    grounding = _Grounding(issue, ref, final_valid=True)
    first = ValidationResult(False, [issue])
    contract = CorrectionContract.from_validation(first)
    assert contract.required_figures, name
    assert contract.missing_figures("draft without declaration") == ("12.5",)

    # This second draft is still invalid and has silently dropped the claim.
    messages = []
    retry = _run(
        grounding, "still-invalid: metric omitted", revisions=1,
        contract=contract, messages=messages,
    )
    assert retry.retry, name
    assert retry.contract_retries == 1
    assert retry.pending_contract is contract
    assert "12.5" in messages[-1]["content"]
    assert ref in messages[-1]["content"]
    if name == "benchmark":
        assert "target_scope=entity:SYNTH_A" in messages[-1]["content"]
    if name == "aggregate":
        assert "target_scope=aggregate" in messages[-1]["content"]

    # The bounded correction restores the exact synthetic observation and passes.
    released = _run(
        grounding,
        _declared(ref=ref),
        revisions=1,
        contract=retry.pending_contract,
        contract_retries=retry.contract_retries,
        iteration=3,
    )
    assert not released.retry
    assert "12.5 | observed" in released.content
    assert not grounding.redacted


def test_contract_retry_is_not_repeated_after_its_one_attempt(scenario):
    _, issue, ref = scenario
    grounding = _Grounding(issue, ref, final_valid=False)
    contract = CorrectionContract.from_validation(ValidationResult(False, [issue]))
    result = _run(
        grounding,
        "still-invalid: metric omitted",
        revisions=2,
        contract=contract,
        contract_retries=1,
        max_revisions=2,
    )
    assert not result.retry
    assert result.contract_retries == 1
    assert grounding.redacted


def test_contract_retry_does_not_run_when_the_correction_budget_is_exhausted(scenario):
    _, issue, ref = scenario
    grounding = _Grounding(issue, ref, final_valid=False)
    contract = CorrectionContract.from_validation(ValidationResult(False, [issue]))
    result = _run(
        grounding,
        "still-invalid: metric omitted",
        revisions=2,
        contract=contract,
        contract_retries=0,
        max_revisions=2,
    )
    assert not result.retry
    assert result.contract_retries == 0
    assert grounding.redacted


def test_valid_draft_that_drops_a_required_figure_still_fails_closed(scenario):
    _, issue, ref = scenario
    grounding = _Grounding(issue, ref, final_valid=True)
    contract = CorrectionContract.from_validation(ValidationResult(False, [issue]))
    result = _run(
        grounding,
        "restored: narrative without the required metric",
        revisions=2,
        contract=contract,
        contract_retries=1,
        max_revisions=2,
    )
    assert not result.retry
    assert grounding.redacted
    assert "omitted" in result.content
