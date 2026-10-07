"""Explicit descriptive labels must leave one complete, evidenced formula."""
import ast

import pytest

from src.agent.grounding.policies import _formula_in_note
from src.agent.grounding.repair_contract import CorrectionContract, directive_for_issue
from tests.test_grounding_derived_preservation_gate import A, B, T, corrected, draft, ledger

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('label', [
    'ratio reduction', 'x', 'a long descriptive label ' * 20,
    'déduction proposée', '結果の説明', 'نتيجة الحساب', 'scenario 25',
    'scenario: derived value', 'compound-label',
])
@pytest.mark.parametrize('formula', [
    '(125 / 10000) * 100', '125 * 0.25 / 10000 * 100',
    '(125 + 250) / 10000 * 100', ' (125 − 25) ÷ 10000 × 100 ',
    '2.926429% × 25%', '125 * (1 - .50)',
])
def test_label_extraction_preserves_the_complete_ast(label, formula):
    expected = _formula_in_note(formula)
    actual = _formula_in_note(label + ': ' + formula)
    assert actual is not None and expected is not None
    assert actual[:2] == expected[:2]
    assert ast.dump(actual[2]) == ast.dump(expected[2])


@pytest.mark.parametrize('note', [
    'estimated reduction in percentage points', '125 / 10000 or 250 / 10000',
    'scenario 25: expected reduction', 'result: 125 /',
    'result: 125 / 10000 * 100 maybe', 'result: 125 / 10000 * 100 # maybe',
    'result: 125 / 10000 * 100; 250 / 10000 * 100',
    '125 / 10000: 250 / 10000',
    'first: 125 / 10000: second: 250 / 10000',
    'first: 125 / 10000 or 250 / 10000: result: 125 / 10000',
    'result:: 125 / 10000', ': 125 / 10000', 'result: 25',
    'result: A / TOTAL * 100', 'result: 125 // 10000', 'result: 2 ** 10',
    'result: 125 / 0', 'result: __import__("os")',
    'result: 125 / 10000 = .0125', 'result: `125 / 10000`',
    'result: 125 / 10000\ntext', '1 - 2 inconnué: 125 / 10000',
])
def test_ambiguous_incomplete_or_trailing_text_is_not_extracted(note):
    assert _formula_in_note(note) is None


@pytest.mark.parametrize('note,value', [
    ('125 / 10000 * 100', 1.25), ('125 / 10000 * 100 = 1.25', 1.25),
    ('125 / 10000 * 100 ≈ 1.25', 1.25), ('125 / 10000 * 100; explanation', 1.25),
    ('125 / 10000 * 100，説明', 1.25), ('等权 125 − 风险平价 25', 100),
])
def test_existing_unprefixed_formats_are_unchanged(note, value):
    assert _formula_in_note(note)[0] == value


@pytest.mark.parametrize('formula,value,paths,entity', [
    ('125 / 10000 * 100', 1.25, [A, T], 'ENTITY_A'),
    ('125 * .25 / 10000 * 100', .3125, [A, T], 'ENTITY_A'),
    ('125 * .50 / 10000 * 100', .625, [A, T], 'ENTITY_A'),
    ('(125+250) / 10000 * 100', 3.75, [A, B, T], 'Report'),
])
def test_labelled_claim_passes_the_complete_repair_lifecycle(tmp_path, formula, value, paths, entity):
    g = ledger(tmp_path)
    validation = g.validate_final_answer(draft(
        'ratio reduction: ' + formula, value, ['wrong_id::' + p for p in paths], entity=entity))
    issue, = validation.issues
    assert directive_for_issue(issue).preserve
    assert issue['derived_repair_verified'] is True
    assert issue['derive_operand_refs'] == [
        ('call_T' if p == T else 'call_A') + '::' + p for p in paths]
    revised = corrected(issue)
    contract = CorrectionContract.from_validation(validation)
    assert g.revalidate(revised).valid
    assert not contract.missing_figures(revised)
    for mutation in [
        'Removed.', revised.replace('| derived |', '| observed |'),
        revised.replace('; call_T::' + T, ''),
        revised.replace('call_T::' + T, 'other::' + T),
        revised.replace(issue['derive_formula'], '125 * 1'),
        revised.replace(issue['value'], '999.000000'),
    ]:
        assert contract.missing_figures(mutation)


@pytest.mark.parametrize('conflict', [
    'snapshot', 'as_of', 'date_as_of', 'trade_date_as_of', 'currency',
    'unit', 'ambiguous', 'entity', 'missing', 'invented',
])
def test_new_extraction_does_not_bypass_evidence_gates(tmp_path, conflict):
    g = ledger(tmp_path, conflict=conflict, equal=conflict == 'entity')
    paths = [B if conflict == 'entity' else A, T]
    if conflict == 'missing':
        paths = [A]
    if conflict == 'unit':
        paths[-1] = 'data.totals.weight'
    formula = '126 / 10000 * 100' if conflict == 'invented' else '125 / 10000 * 100'
    v = g.validate_final_answer(draft('result: ' + formula, 1.25,
                                    ['wrong_id::' + p for p in paths]))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)


@pytest.mark.parametrize('with_calc', [False, True])
@pytest.mark.parametrize('formula', [
    'result: 125 / 10000 * 100 maybe', 'result: 125 /',
    'first: 125 / 10000: second: 125 / 10000 * 100',
    'scenario 25: expected reduction',
])
def test_unextractable_notes_cannot_be_preserved_by_calc_hints(tmp_path, with_calc, formula):
    g = ledger(tmp_path, calc='125 / 10000 * 100' if with_calc else None)
    v = g.validate_final_answer(draft(formula))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)
    assert all(i.get('reason') == 'formula_not_evaluable' for i in v.issues)
