"""Exact operand contracts for structured, snapshot-bound financial arithmetic."""
import copy
import json

import pytest

from src.agent.grounding import GroundingLedger

pytestmark = pytest.mark.unit
TOTAL = 222105473.58200002


def payload():
    return {'context': {'snapshot_id': 'snapshot-a', 'as_of': '2026-10-07T03:31:01+00:00',
                        'totals': {'portfolio_market_value_ars': TOTAL},
                        'holdings': [
                            {'symbol': 'GGAL', 'currency': 'ARS', 'market_value_ars': 6615045.0, 'weight_portfolio': 0.02978335},
                            {'symbol': 'BBAR', 'currency': 'ARS', 'market_value_ars': 1384425.0, 'weight_portfolio': 0.00623319}]}}


def ingest(gate, data, call='snapshot-call'):
    gate.ingest_tool_result(tool_name='portfolio_summary', arguments={'view': 'compact'},
                           result=json.dumps(data), call_id=call, success=True)


def validate(tmp_path, expr, refs, value, symbol='GGAL', data=None, other=None):
    gate = GroundingLedger(run_dir=tmp_path, user_message='Analyze portfolio')
    ingest(gate, data or payload())
    if other:
        ingest(gate, other, 'other-call')
    content = f'{symbol or "Portfolio"}: {value:.6f} puntos porcentuales.\n\n```figures\n{value:.6f} | derived | {expr} | {refs}\n```'
    return gate.validate_final_answer(content)


def ref(path, call='snapshot-call'):
    return call + '::context.' + path


G = ref('holdings[0].market_value_ars')
B = ref('holdings[1].market_value_ars')
T = ref('totals.portfolio_market_value_ars')
W = ref('holdings[0].weight_portfolio')


@pytest.mark.parametrize('expr,refs,value,symbol', [
    ('1384425.0 / 222105473.58200002 * 100', B + '; ' + T, 0.623319, 'BBAR'),
    ('6615045.0 * 0.25 / 222105473.58200002 * 100', G + '; ' + T, 0.744584, 'GGAL'),
    ('6615045.0 * 0.50 / 222105473.58200002 * 100', G + '; ' + T, 1.489167, 'GGAL'),
    ('0.02978335 * 0.25 * 100', W, 0.744584, 'GGAL'),
    ('(6615045 + 1384425) / 222105473.58200002 * 100', G + '; ' + B + '; ' + T, (6615045 + 1384425) / TOTAL * 100, None),
])
def test_legitimate_exact_operand_derivations(tmp_path, expr, refs, value, symbol):
    result = validate(tmp_path, expr, refs, value, symbol)
    assert result.valid, result.issues


@pytest.mark.parametrize('expr,refs,symbol', [
    ('999999 / 222105473.58200002 * 100', G + '; ' + T, 'GGAL'),
    ('1384425 / 222105473.58200002 * 100', B + '; ' + T, 'GGAL'),
    ('6615045 / 222105473.58200002 * 100', G + '; ' + ref('missing'), 'GGAL'),
    ('6615044 / 222105473.58200002 * 100', G + '; ' + T, 'GGAL'),
    ('(6615045 + 1000) / 222105473.58200002 * 100', G + '; ' + T, 'GGAL'),
    ('(6615045 - 1000) / 222105473.58200002 * 100', G + '; ' + T, 'GGAL'),
    ('6615045 / 222105473.58200002 * 100', G, 'GGAL'),
    ('0.25 / 222105473.58200002 * 100', T, 'GGAL'),
])
def test_invalid_financial_leaves(tmp_path, expr, refs, symbol):
    value = eval(expr, {'__builtins__': {}}, {})
    assert not validate(tmp_path, expr, refs, value, symbol).valid


@pytest.mark.parametrize('mutation', ['snapshot', 'as_of', 'currency', 'no_snapshot'])
def test_cross_call_temporal_and_currency_guards(tmp_path, mutation):
    other = copy.deepcopy(payload())
    if mutation == 'snapshot':
        other['context']['snapshot_id'] = 'snapshot-b'
    elif mutation == 'as_of':
        other['context']['as_of'] = '2026-10-08T03:31:01+00:00'
    elif mutation == 'currency':
        other['context']['totals']['currency'] = 'USD'
    else:
        del other['context']['snapshot_id']
    result = validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                      G + '; ' + ref('totals.portfolio_market_value_ars', 'other-call'),
                      6615045 / TOTAL * 100, other=other)
    assert not result.valid, result.issues
    expected = ('operand_snapshot_conflict' if mutation in {'snapshot', 'as_of'}
                else 'operand_snapshot_unavailable' if mutation == 'no_snapshot'
                else 'operand_unit_conflict')
    assert any(issue.get('reason') == expected for issue in result.issues), result.issues


def test_equal_numbers_do_not_override_wrong_entity_ref(tmp_path):
    data = payload()
    data['context']['holdings'][1]['market_value_ars'] = 6615045
    assert not validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                        B + '; ' + T, 6615045 / TOTAL * 100, data=data).valid


def test_same_snapshot_exact_refs_across_calls_are_explicitly_compatible(tmp_path):
    result = validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                      G + '; ' + ref('totals.portfolio_market_value_ars', 'other-call'),
                      6615045 / TOTAL * 100, other=payload())
    assert result.valid, result.issues


def test_no_snapshot_atomic_call_is_compatible_but_cross_call_is_not(tmp_path):
    data = payload()
    del data['context']['snapshot_id']
    assert validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                    G + '; ' + T, 6615045 / TOTAL * 100, data=data).valid
    assert not validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                        G + '; ' + ref('totals.portfolio_market_value_ars', 'other-call'),
                        6615045 / TOTAL * 100, data=data, other=data).valid


def test_nested_as_of_conflict_within_one_call(tmp_path):
    data = payload()
    data['context']['totals']['as_of'] = '2026-10-08T03:31:01+00:00'
    assert not validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                        G + '; ' + T, 6615045 / TOTAL * 100, data=data).valid


def test_currency_and_unit_conflicts_within_one_call(tmp_path):
    for overrides in [{'currency': 'USD'}, {'_units': {'portfolio_market_value_ars': 'ratio'}}]:
        data = payload()
        data['context']['totals'].update(overrides)
        assert not validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                            G + '; ' + T, 6615045 / TOTAL * 100, data=data).valid


def test_equal_declared_values_are_ambiguous(tmp_path):
    data = payload()
    data['context']['holdings'][1]['market_value_ars'] = 6615045
    assert not validate(tmp_path, '(6615045 + 6615045) / 222105473.58200002 * 100',
                        G + '; ' + B + '; ' + T, 2 * 6615045 / TOTAL * 100, None, data=data).valid


def test_financial_rigor_metadata_remains_compatible(tmp_path):
    gate = GroundingLedger(run_dir=tmp_path, user_message='Analyze BBAR portfolio')
    ingest(gate, payload())
    expr = '1384425 / 222105473.58200002 * 100'
    gate.ingest_tool_result(tool_name='financial_rigor', arguments={'command': 'calc', 'expr': expr},
                           result=json.dumps({'result': 1384425 / TOTAL * 100}), call_id='calc-call', success=True)
    result = gate.validate_final_answer('BBAR: 0,623319 puntos porcentuales.\n\n```figures\n'
                                        '0.623319 | derived | ' + expr + ' | ' + B + '; ' + T + '\n```')
    assert result.valid, result.issues


def test_unobserved_intermediate_is_not_promoted_to_evidence(tmp_path):
    assert not validate(tmp_path, '1653761.25 / 222105473.58200002 * 100',
                        G + '; ' + T, 0.744584).valid


def test_snapshot_metadata_persists_and_survives_reingestion(tmp_path):
    gate = GroundingLedger(run_dir=tmp_path, user_message='Analyze portfolio')
    ingest(gate, payload())
    ingest(gate, payload(), 'replayed-call')
    artifact = json.loads((tmp_path / 'artifacts' / 'grounding_evidence.json').read_text())
    financial = [r for r in artifact['evidence'] if r['unit'] in {'money', 'ratio'}]
    assert financial
    assert all(r['snapshot_id'] == 'snapshot-a' for r in financial)
    result = gate.validate_final_answer('GGAL: 2,978335 puntos porcentuales.\n\n```figures\n'
                                        '2.978335 | derived | 6615045 / 222105473.58200002 * 100 | '
                                        + G + '; ' + ref('totals.portfolio_market_value_ars', 'replayed-call') + '\n```')
    assert result.valid, result.issues
