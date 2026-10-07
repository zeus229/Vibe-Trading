"""Every repair producer must prove a complete derived claim before preserve."""
import json
from types import SimpleNamespace

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.figures import Figure
from src.agent.grounding.repair_contract import CorrectionContract, directive_for_issue

pytestmark = pytest.mark.unit
A = 'data.rows[0].market_value'
B = 'data.rows[1].market_value'
T = 'data.totals.market_value'


def ledger(tmp_path, *, conflict=None, calc=None, equal=False):
    gate = GroundingLedger(run_dir=tmp_path, user_message='ENTITY_A and ENTITY_B scenarios')
    data = {'data': {'snapshot_id': 's1', 'as_of': '2026-10-07T00:00:00Z',
                    'date': '2026-10-07', 'trade_date': '2026-10-07',
                    'rows': [{'entity_id': 'ENTITY_A', 'currency': 'USD', 'market_value': 125},
                             {'entity_id': 'ENTITY_B', 'currency': 'USD', 'market_value': 125 if equal else 250}]}}
    total = {'data': {'snapshot_id': 's1', 'as_of': '2026-10-07T00:00:00Z',
                     'date': '2026-10-07', 'trade_date': '2026-10-07',
                     'totals': {'currency': 'USD', 'market_value': 10000}}}
    if conflict in {'snapshot', 'as_of', 'date_as_of', 'trade_date_as_of'}:
        if conflict == 'snapshot':
            total['data']['snapshot_id'] = 's2'
        else:
            total['data']['as_of'] = '2026-10-08T00:00:00Z'
    if conflict == 'currency':
        total['data']['totals']['currency'] = 'EUR'
    if conflict == 'unit':
        total['data']['totals']['weight'] = total['data']['totals'].pop('market_value')
    for call, payload in [('call_A', data), ('call_T', total)]:
        gate.ingest_tool_result(tool_name='read_measurements', arguments={}, result=json.dumps(payload), call_id=call, success=True)
    if conflict == 'ambiguous':
        gate.ingest_tool_result(tool_name='read_measurements', arguments={}, result=json.dumps(data), call_id='call_OTHER', success=True)
    if calc is not None:
        gate.ingest_tool_result(tool_name='financial_rigor', arguments={'command': 'calc', 'expr': calc},
                               result=json.dumps({'result': eval(calc, {'__builtins__': {}}, {})}), call_id='call_calc', success=True)
    return gate


def draft(formula='125 / 10000 * 100', value=1.25, refs=None, shape=False, entity='ENTITY_A'):
    refs = refs if refs is not None else ['wrong_id::' + A, 'wrong_id::' + T]
    return (f'{entity} change {value:.6f} pp.\n```figures\n'
            f'{value:.6f}{"%" if shape else ""} | derived | {formula} | ' + '; '.join(refs) + '\n```')


def corrected(issue):
    return (f'{issue.get("symbol") or "Report"} change {issue["value"]} pp.\n```figures\n'
            f'{issue["value"]} | derived | {issue["derive_formula"]} | '
            + '; '.join(issue['derive_operand_refs']) + '\n```')


@pytest.mark.parametrize('with_calc', [False, True])
@pytest.mark.parametrize('shape', [False, True])
@pytest.mark.parametrize('formula,value,paths,entity', [
    ('125 / 10000 * 100', 1.25, [A, T], 'ENTITY_A'),
    ('125 * .25 / 10000 * 100', .3125, [A, T], 'ENTITY_A'),
    ('125 * .50 / 10000 * 100', .625, [A, T], 'ENTITY_A'),
    ('(125+250) / 10000 * 100', 3.75, [A, B, T], 'Report'),
    ('125+250', 375, [A, B], 'Report'),
    ('125*1', 125, [A], 'ENTITY_A'),
])
def test_preserve_implies_full_revalidation(tmp_path, with_calc, shape, formula, value, paths, entity):
    g = ledger(tmp_path, calc=formula if with_calc else None)
    validation = g.validate_final_answer(draft(formula, value, ['wrong_id::' + p for p in paths], shape, entity))
    assert not validation.valid
    issue, = validation.issues
    assert directive_for_issue(issue).preserve, issue
    expected = [('call_T' if p == T else 'call_A') + '::' + p for p in paths]
    assert issue['derive_operand_refs'] == expected
    revised = corrected(issue)
    assert g.revalidate(revised).valid
    assert not CorrectionContract.from_validation(validation).missing_figures(revised)


@pytest.mark.parametrize('with_calc', [False, True])
@pytest.mark.parametrize('shape', [False, True])
@pytest.mark.parametrize('missing', [A, T, B])
def test_no_producer_may_fill_an_omitted_financial_ref(tmp_path, with_calc, shape, missing):
    formula = '(125+250)/10000*100' if missing == B else '125/10000*100'
    value = 3.75 if missing == B else 1.25
    paths = [A, B, T] if missing == B else [A, T]
    g = ledger(tmp_path, calc=formula if with_calc else None)
    v = g.validate_final_answer(draft(formula, value, ['wrong_id::'+p for p in paths if p != missing], shape, 'Report' if missing == B else 'ENTITY_A'))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues), v.issues
    assert not CorrectionContract.from_validation(v).required_figures


@pytest.mark.parametrize('with_calc', [False, True])
@pytest.mark.parametrize('shape', [False, True])
@pytest.mark.parametrize('conflict', ['snapshot', 'as_of', 'date_as_of', 'trade_date_as_of', 'currency', 'unit', 'entity', 'ambiguous', 'wrong_value', 'invented'])
def test_conflicts_never_become_preservable_hints(tmp_path, with_calc, shape, conflict):
    formula = '125 / 10000 * 100'
    g = ledger(tmp_path, conflict=conflict, calc=formula if with_calc else None, equal=conflict == 'entity')
    refs = ['wrong_id::' + (B if conflict == 'entity' else A), 'wrong_id::' + ('data.totals.weight' if conflict == 'unit' else T)]
    if conflict == 'wrong_value':
        formula = '124 / 10000 * 100'
    if conflict == 'invented':
        formula = '(125+999) / 10000 * 100'
    v = g.validate_final_answer(draft(formula, refs=refs, shape=shape))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues), v.issues


@pytest.mark.parametrize('mutation', ['drop', 'observed', 'formula', 'value', 'missing', 'substitute', 'extra'])
def test_correction_contract_enforces_complete_derived_identity(tmp_path, mutation):
    g = ledger(tmp_path, calc='125/10000*100')
    v = g.validate_final_answer(draft())
    issue, = v.issues
    assert directive_for_issue(issue).preserve
    text = corrected(issue)
    if mutation == 'drop':
        text = 'No figure.'
    elif mutation == 'observed':
        text = text.replace('| derived |', '| observed |')
    elif mutation == 'formula':
        text = text.replace(issue['derive_formula'], '125*100/10000')
    elif mutation == 'value':
        text = text.replace(issue['value'], '9.99')
    elif mutation == 'missing':
        text = text.replace('; call_T::'+T, '')
    elif mutation == 'substitute':
        text = text.replace('call_T::'+T, 'call_A::'+B)
    else:
        text = text.replace('\n```', '; call_A::'+B+'\n```')
    assert CorrectionContract.from_validation(v).missing_figures(text) == (issue['value'],)


@pytest.mark.parametrize('authorization', [None, False, "true", "false", 1])
def test_unverified_metadata_is_not_an_authorization(authorization):
    issue = {'role': 'derived', 'derive_formula': '125/10000*100',
             'derive_operand_refs': ['call_A::'+A, 'call_T::'+T]}
    issue['derived_repair_verified'] = authorization
    assert not directive_for_issue(issue).preserve


@pytest.mark.parametrize('with_calc', [False, True])
@pytest.mark.parametrize('formula,value', [
    ('125*2', 250), ('125+125', 250), ('125-(-125)', 250),
    ('125*100-125*99', 125),
    ('125*100-125*100*.99', 125), ('125*(1-.99)*100', 125),
    ('125*100', 12500), ('125*(1-.99)*100*100', 12500),
    ('125-125', 0), ('125+125-125', 125),
])
def test_equivalent_supported_forms_preserve_same_exact_ref(tmp_path, with_calc, formula, value):
    g = ledger(tmp_path, calc=formula if with_calc else None)
    v = g.validate_final_answer(draft(formula, value, ['wrong_id::'+A]))
    issue, = v.issues
    assert directive_for_issue(issue).preserve, issue
    assert issue['derive_operand_refs'] == ['call_A::'+A]
    assert g.revalidate(corrected(issue)).valid


@pytest.mark.parametrize('with_calc', [False, True])
@pytest.mark.parametrize('formula,value', [
    ('125*200', 25000), ('125*100+125*100', 25000),
    ('125/10000', .0125), ('125/100/100', .0125),
    ('125*(1/10000)', .0125), ('125*.0001', .0125),
    ('125*(1/10000)*100', 1.25),
    ('125*(1/100)*(.01)*100', 1.25),
])
def test_scale_and_hidden_denominator_are_fail_closed_across_producers(tmp_path, with_calc, formula, value):
    g = ledger(tmp_path, calc=formula if with_calc else None)
    v = g.validate_final_answer(draft(formula, value, ['wrong_id::'+A]))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues), v.issues


def test_undeclared_calc_candidate_requires_shared_authorization(tmp_path):
    g = ledger(tmp_path, calc='125/10000*100')
    v = g.validate_final_answer('ENTITY_A change 1.25 pp.\n```figures\n```')
    issue, = v.issues
    assert directive_for_issue(issue).preserve, issue
    assert issue['derived_repair_verified'] is True
    assert g.revalidate(corrected(issue)).valid


@pytest.mark.parametrize('conflict', ['snapshot', 'as_of', 'currency', 'unit'])
def test_undeclared_calc_conflicts_cannot_avoid_gate(tmp_path, conflict):
    g = ledger(tmp_path, conflict=conflict, calc='125/10000*100')
    v = g.validate_final_answer('ENTITY_A change 1.25 pp.\n```figures\n```')
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)


def test_numerically_equal_observed_does_not_allow_correction_downgrade(tmp_path):
    g = ledger(tmp_path, calc='125/10000*100')
    g.ingest_tool_result(tool_name='read_measurements', arguments={},
                         result=json.dumps({'entity_id': 'ENTITY_A', 'currency': 'USD', 'weight': .0125}), call_id='call_W', success=True)
    v = g.validate_final_answer(draft(value=1.25, shape=True))
    issue, = v.issues
    assert directive_for_issue(issue).preserve
    correction = 'ENTITY_A change 1.25%.\n```figures\n1.25% | observed | weight | call_W::weight\n```'
    assert g.revalidate(correction).valid
    assert CorrectionContract.from_validation(v).missing_figures(correction)


def test_single_literal_note_stays_outside_existing_formula_contract(tmp_path):
    g = ledger(tmp_path)
    v = g.validate_final_answer(draft('125', 125, ['wrong_id::'+A]))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)
    direct = g.revalidate(draft('125', 125, ['call_A::'+A]))
    assert not direct.valid
    assert any(i.get('reason') == 'formula_not_evaluable' for i in direct.issues)


@pytest.mark.parametrize('total', [2000, 5000, 10000])
@pytest.mark.parametrize('refs', [['wrong_id::'+A], ['call_A::'+A]])
def test_original_denominator_blocker_with_calc_and_correct_shape(tmp_path, total, refs):
    formula = f'125/{total}*100'
    # Use a single compatible total at the required magnitude.
    g2 = GroundingLedger(run_dir=tmp_path/'actual', user_message='ENTITY_A scenario')
    g2.ingest_tool_result(tool_name='read_measurements', arguments={}, result=json.dumps({
        'snapshot_id': 's1', 'as_of': '2026-10-07T00:00:00Z',
        'data': {'rows': [{'entity_id': 'ENTITY_A', 'currency': 'USD', 'market_value': 125}],
                 'totals': {'currency': 'USD', 'market_value': total}}}), call_id='call_A', success=True)
    g2.ingest_tool_result(tool_name='financial_rigor', arguments={'command': 'calc', 'expr': formula},
                          result=json.dumps({'result': 125/total*100}), call_id='call_calc', success=True)
    v = g2.validate_final_answer(draft(formula, 125/total*100, refs))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)


@pytest.mark.parametrize('with_calc', [False, True])
@pytest.mark.parametrize('formula,value', [
    ('(125+999)-999', 125), ('125+(999-999)', 125),
    ('125+1', 126), ('125+10000', 10125),
])
def test_cancellation_does_not_hide_unobserved_additive_evidence(tmp_path, with_calc, formula, value):
    g = ledger(tmp_path, calc=formula if with_calc else None)
    v = g.validate_final_answer(draft(formula, value, ['wrong_id::'+A]))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)


@pytest.mark.parametrize('formula', ['125/2', '(125)/2', '125*(1/2)'])
def test_scalar_division_stays_unsupported_for_automatic_repair(tmp_path, formula):
    g = ledger(tmp_path, calc=formula)
    v = g.validate_final_answer(draft(formula, 62.5, ['wrong_id::'+A]))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)


def test_canonical_derived_replacement_passes_shared_proof(tmp_path):
    # The existing canonical producer is schema-specific legacy code; the new
    # authorization boundary is generic and proves its output like any hint.
    g = ledger(tmp_path)
    figure = Figure(
        text='3.00', value=3, percent=False, start=0, end=4, line=0,
        shape='measured', digits='3.00')
    issue = {'role': 'derived', 'value': '3.00', 'figure_value': 3,
             'figure_percent': False, 'figure_digits': '3.00',
             'replacement_role': 'derived', 'replacement_value': 1.25,
             'replacement_text': '1.25', 'replacement_digits': '1.25',
             'replacement_formula': '125/10000*100',
             'replacement_refs': ['call_A::'+A, 'call_T::'+T]}
    g._authorize_derived_repair(issue, figure, None, 'ENTITY_A', g._evidence)
    directive = directive_for_issue(issue)
    assert directive.preserve and directive.action.value == 'replace'
    text = 'ENTITY_A change 1.25 pp.\n```figures\n1.25 | derived | '+issue['replacement_formula']+' | '+'; '.join(issue['replacement_refs'])+'\n```'
    assert g.revalidate(text).valid
    contract = CorrectionContract.from_validation(SimpleNamespace(issues=[issue], passed_figures=()))
    assert not contract.missing_figures(text)


@pytest.mark.parametrize('conflict', ['snapshot', 'as_of', 'currency', 'unit'])
def test_canonical_derived_candidate_cannot_bypass_proof(tmp_path, conflict):
    g = ledger(tmp_path, conflict=conflict, calc='125/10000*100')
    figure = Figure(text='3.00', value=3, percent=False, start=0, end=4, line=0,
                    shape='measured', digits='3.00')
    issue = {'role': 'derived', 'replacement_role': 'derived', 'replacement_value': 1.25,
             'replacement_text': '1.25', 'replacement_digits': '1.25',
             'replacement_formula': '125/10000*100',
             'replacement_refs': ['call_A::'+A, 'call_T::'+('data.totals.weight' if conflict == 'unit' else T)]}
    g._authorize_derived_repair(issue, figure, None, 'ENTITY_A', g._evidence)
    assert not directive_for_issue(issue).preserve


def test_wrong_refs_on_calc_result_cannot_make_calc_an_observation(tmp_path):
    g = ledger(tmp_path, calc='125*1')
    v = g.validate_final_answer(draft('125*1', 125, ['wrong_id::result']))
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)


def test_unrelated_equal_result_calc_cannot_override_proven_derived_intent(tmp_path):
    g = ledger(tmp_path, calc='250*.125/10000*100')
    formula = '125*.25/10000*100'
    v = g.validate_final_answer(draft(formula, .3125))
    issue, = v.issues
    assert directive_for_issue(issue).preserve, issue
    assert issue['derive_operand_refs'] == ['call_A::'+A, 'call_T::'+T]
    assert g.revalidate(corrected(issue)).valid


def test_equal_result_from_different_observed_operands_does_not_change_refs(tmp_path):
    g = ledger(tmp_path, calc='250-125')
    v = g.validate_final_answer(draft('125*1', 125, ['wrong_id::'+A]))
    issue, = v.issues
    assert directive_for_issue(issue).preserve, issue
    assert issue['derive_operand_refs'] == ['call_A::'+A]
    assert g.revalidate(corrected(issue)).valid


@pytest.mark.parametrize('note', ['.3581-.2308', '(.3581-.2308)*100', '100*(.3581-.2308)'])
def test_legacy_calc_coverage_cannot_be_lost_by_display_scaling(tmp_path, note):
    g = GroundingLedger(run_dir=tmp_path, user_message='risk difference')
    for call, value in [('call_A', .3581), ('call_B', .2308)]:
        g.ingest_tool_result(tool_name='portfolio_risk', arguments={},
                             result=json.dumps({'data': {'volatility': {'annualized_vol': value}}}), call_id=call, success=True)
    g.ingest_tool_result(tool_name='financial_rigor', arguments={'command': 'calc', 'expr': '.3581-.2308'},
                         result=json.dumps({'result': .1273}), call_id='call_calc', success=True)
    text = 'Difference 12.73%.\n```figures\n12.73% | derived | '+note+' | call_A::data.volatility.annualized_vol\n```'
    v = g.validate_final_answer(text)
    assert not v.valid
    assert all(not directive_for_issue(i).preserve for i in v.issues)
