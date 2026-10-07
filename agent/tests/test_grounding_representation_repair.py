"""Structured provenance survives display units and proven intermediate notation."""
import json
from dataclasses import replace

import pytest

from src.agent.grounding import GroundingLedger
from src.agent.grounding.repair_contract import CorrectionContract, directive_for_issue

pytestmark = pytest.mark.unit
A = 'data.rows[0].market_value'
W = 'data.rows[0].weight'
B = 'data.rows[1].weight'
T = 'data.totals.market_value'
CALL = 'call_data'


def ledger(tmp_path, *, mutation=None):
    g = GroundingLedger(run_dir=tmp_path, user_message='ENTITY_A scenarios')
    data = {'data': {'snapshot_id': 's1', 'as_of': '2026-10-07T00:00:00Z',
                     'rows': [{'entity_id': 'ENTITY_A', 'currency': 'USD', 'market_value': 125, 'weight': .025},
                              {'entity_id': 'ENTITY_B', 'currency': 'USD', 'market_value': 125, 'weight': .025}],
                     'totals': {'currency': 'USD', 'market_value': 5000}}}
    g.ingest_tool_result(tool_name='measurements', arguments={}, result=json.dumps(data), call_id=CALL, success=True)
    if mutation == 'ambiguous':
        g.ingest_tool_result(tool_name='measurements', arguments={}, result=json.dumps(data), call_id='call_other', success=True)
    if mutation in {'snapshot', 'as_of', 'currency', 'unit'}:
        field = T if mutation != 'unit' else W
        r = next(r for r in g._evidence if r.field == field)
        change = {'snapshot': {'snapshot_id': 's2'}, 'as_of': {'as_of': '2026-10-08T00:00:00Z'},
                  'currency': {'currency': 'EUR'}, 'unit': {'unit': 'money'}}[mutation]
        g._evidence[g._evidence.index(r)] = replace(r, **change)
    return g


def declared(text, lines):
    return text + '\n```figures\n' + '\n'.join(lines) + '\n```'


def issue_for(g, text, value):
    v = g.validate_final_answer(text)
    found = [i for i in v.issues if float(i.get('figure_value', -1)) == value]
    assert found, v.issues
    return v, found[-1]


def revised(i):
    return declared(f"ENTITY_A result {i['value']} percentage points.", [
        f"{i['value']} | derived | {i['derive_formula']} | " + '; '.join(i['derive_operand_refs'])])


@pytest.mark.parametrize('notation', ['percentage points', 'points', '单位'])
@pytest.mark.parametrize('prefix', [CALL, 'arbitrary_id'])
def test_observed_percent_requires_explicit_proven_display_conversion(tmp_path, notation, prefix):
    g = ledger(tmp_path)
    text = declared(f'ENTITY_A: 25% of 2.5 {notation}.', [
        f'2.5% | observed | observed ratio | {prefix}::{W}', '25% | count | scalar'])
    v, i = issue_for(g, text, 2.5)
    assert directive_for_issue(i).preserve and i['derived_repair_verified']
    assert i['derive_operand_refs'] == [CALL + '::' + W]
    assert g.revalidate(revised(i)).valid
    contract = CorrectionContract.from_validation(v)
    assert not contract.missing_figures(revised(i))
    assert contract.missing_figures(revised(i).replace('| derived |', '| observed |'))
    assert not v.valid  # Unit mismatch itself is never silently accepted.


@pytest.mark.parametrize('factor,result', [('.25', '.625'), ('.50', '1.25')])
@pytest.mark.parametrize('formula,paths', [
    ('125 * {factor} / 5000 * 100', [A, T]),
    ('{factor} * (125 / 5000 * 100)', [A, T]),
    ('.025 * {factor}', [W]),
    ('{factor} * .025 * 100', [W]),
])
def test_supported_direct_and_observed_ratio_forms(tmp_path, factor, result, formula, paths):
    g = ledger(tmp_path)
    formula = formula.format(factor=factor)
    value = str(float(result))
    text = declared('ENTITY_A reduction ' + value + ' percentage points.', [
        value + ' | derived | ratio change: ' + formula + ' | ' + '; '.join('wrong::' + p for p in paths)])
    _, i = issue_for(g, text, float(result))
    assert directive_for_issue(i).preserve
    assert i['derive_operand_refs'] == [CALL + '::' + p for p in paths]
    assert g.revalidate(revised(i)).valid


def chain_text(*, producer='125 * .25', producer_refs=None, consumer_refs=None, extra=None):
    producer_refs = producer_refs if producer_refs is not None else ['wrong::' + A]
    consumer_refs = consumer_refs if consumer_refs is not None else ['wrong::' + A, 'wrong::' + T]
    lines = [
        '31.25 | derived | ' + producer + ' | ' + '; '.join(producer_refs),
        '0.625% | derived | 31.25 / 5000 * 100 | ' + '; '.join(consumer_refs)]
    if extra:
        lines.append(extra)
    return declared('ENTITY_A change 0.625 percentage points.', lines)


def test_proven_declared_intermediate_is_inlined_without_creating_evidence(tmp_path):
    g = ledger(tmp_path)
    before = tuple(g._evidence)
    v, i = issue_for(g, chain_text(), .625)
    assert directive_for_issue(i).preserve and i['derived_repair_verified']
    assert i['derive_operand_refs'] == [CALL + '::' + A, CALL + '::' + T]
    assert '31.25' not in i['derive_formula']
    assert g.revalidate(revised(i)).valid
    assert not CorrectionContract.from_validation(v).missing_figures(revised(i))
    assert tuple(g._evidence) == before


@pytest.mark.parametrize('mutation', [
    'missing_source', 'missing_total', 'wrong_source', 'bad_formula', 'ambiguous',
    'producer_percent', 'producer_extra_ref', 'snapshot', 'as_of', 'currency',
])
def test_declared_dependency_does_not_fill_refs_or_bypass_provenance(tmp_path, mutation):
    g = ledger(tmp_path, mutation=mutation)
    kw = {}
    if mutation == 'missing_source':
        kw['consumer_refs'] = ['wrong::' + T]
    if mutation == 'missing_total':
        kw['consumer_refs'] = ['wrong::' + A]
    if mutation == 'wrong_source':
        kw['producer_refs'] = ['wrong::data.rows[1].market_value']
    if mutation == 'bad_formula':
        kw['producer'] = '126 * .25'
    if mutation == 'producer_extra_ref':
        kw['producer_refs'] = ['wrong::' + A, 'wrong::' + T]
    text = chain_text(**kw)
    if mutation == 'producer_percent':
        text = text.replace('31.25 |', '31.25% |')
    _, i = issue_for(g, text, .625)
    assert not directive_for_issue(i).preserve
    assert not i.get('derived_repair_verified')


def test_two_valid_dependencies_with_equal_values_are_ambiguous(tmp_path):
    g = ledger(tmp_path)
    text = chain_text(extra='31.25 | derived | 125 * 0.25 | wrong::' + A)
    _, i = issue_for(g, text, .625)
    assert not directive_for_issue(i).preserve


@pytest.mark.parametrize('mutation', ['entity', 'ambiguous', 'unit', 'missing', 'currency_mark', 'wrong_value'])
def test_ratio_display_repair_is_fail_closed(tmp_path, mutation):
    g = ledger(tmp_path, mutation=mutation)
    ref = 'wrong::' + (B if mutation == 'entity' else W)
    if mutation == 'missing':
        ref = 'wrong::data.missing'
    text = declared('ENTITY_A: ' + ('$2.5' if mutation == 'currency_mark' else '2.5') + ' units.', [
        ('2.6%' if mutation == 'wrong_value' else '2.5%') + ' | observed | ratio | ' + ref])
    _, i = issue_for(g, text, 2.5)
    assert not directive_for_issue(i).preserve


@pytest.mark.parametrize('producer', [
    '31.25 * 1', '999 * .25', '125 * .0001', '125 * 200',
    '31.25 / 1', '125 / 4',
])
def test_unproven_or_unsupported_producers_cannot_be_inlined(tmp_path, producer):
    g = ledger(tmp_path)
    _, i = issue_for(g, chain_text(producer=producer), .625)
    assert not directive_for_issue(i).preserve


def test_display_conversion_does_not_choose_between_declarations(tmp_path):
    g = ledger(tmp_path)
    text = declared('ENTITY_A: 2.5 points.', [
        '2.5% | observed | first | wrong::' + W,
        '2.5% | observed | other | wrong::' + B])
    _, i = issue_for(g, text, 2.5)
    assert not directive_for_issue(i).preserve


def test_declared_dependencies_keep_decimal_literals_and_coefficients(tmp_path):
    g = ledger(tmp_path)
    text = declared('ENTITY_A change 2.475 points.', [
        '123.75 | derived | 125 * .99 | wrong::' + A,
        '2.475 | derived | 123.75 / 5000 * 100 | wrong::' + A + '; wrong::' + T])
    _, i = issue_for(g, text, 2.475)
    assert directive_for_issue(i).preserve
    assert '.99' in i['derive_formula']
    assert g.revalidate(revised(i)).valid


def test_display_contract_preserves_the_exact_observation_across_time(tmp_path):
    g = ledger(tmp_path)
    text = declared('ENTITY_A: 2.5 points.', ['2.5% | observed | ratio | ' + CALL + '::' + W])
    v, i = issue_for(g, text, 2.5)
    data = {'data': {'snapshot_id': 's2', 'as_of': '2026-10-08T00:00:00Z',
                     'rows': [{'entity_id': 'ENTITY_A', 'currency': 'USD', 'weight': .025}]}}
    g.ingest_tool_result(tool_name='measurements', arguments={}, result=json.dumps(data), call_id='call_later', success=True)
    changed = revised(i).replace(CALL + '::' + W, 'call_later::' + W)
    assert g.revalidate(changed).valid  # A separate observation is not the promised observation.
    assert CorrectionContract.from_validation(v).missing_figures(changed)
