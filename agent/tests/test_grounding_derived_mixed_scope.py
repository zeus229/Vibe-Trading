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


@pytest.mark.parametrize('date_key', ['date', 'trade_date'])
def test_explicit_as_of_cannot_be_hidden_by_other_dates(tmp_path, date_key):
    data = payload()
    data['context'][date_key] = '2026-10-01'
    other = copy.deepcopy(data)
    other['context']['as_of'] = '2026-10-08T03:31:01+00:00'
    result = validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                      G + '; ' + ref('totals.portfolio_market_value_ars', 'other-call'),
                      6615045 / TOTAL * 100, data=data, other=other)
    assert not result.valid, result.issues
    assert any(i.get('reason') == 'operand_snapshot_conflict' for i in result.issues)


@pytest.mark.parametrize('date_key', ['date', 'trade_date'])
def test_matching_explicit_as_of_and_other_dates_are_compatible(tmp_path, date_key):
    data = payload()
    data['context'][date_key] = '2026-10-01'
    result = validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                      G + '; ' + ref('totals.portfolio_market_value_ars', 'other-call'),
                      6615045 / TOTAL * 100, data=data, other=data)
    assert result.valid, result.issues


@pytest.mark.parametrize('expr', [
    '6615045 * (1 - 0.25) / 222105473.58200002 * 100',
    '6615045 * (0.25 * 0.50) / 222105473.58200002 * 100',
])
def test_legitimate_constant_subtrees_are_dimensionless_factors(tmp_path, expr):
    assert validate(tmp_path, expr, G + '; ' + T, eval(expr, {'__builtins__': {}}, {})).valid


@pytest.mark.parametrize('expr', [
    '(6615045 + 10000) / 222105473.58200002 * 100',
    '(6615045 + (100 * 100)) / 222105473.58200002 * 100',
    '(6615045 - (1 - 0.25)) / 222105473.58200002 * 100',
    '(100 * 100) / 222105473.58200002 * 100',
    '6615045 * (100 * 100) / 222105473.58200002 * 100',
    '6615045 * (1 / 0.0001) / 222105473.58200002 * 100',
])
def test_composed_constants_cannot_replace_financial_leaves(tmp_path, expr):
    assert not validate(tmp_path, expr, G + '; ' + T, eval(expr, {'__builtins__': {}}, {})).valid


def test_temporal_fields_and_old_json_roundtrip(tmp_path):
    from dataclasses import asdict

    from src.agent.grounding.evidence import EvidenceRecord

    gate = GroundingLedger(run_dir=tmp_path, user_message='Analyze portfolio')
    data = payload()
    data['context'].update(date='2026-10-01', trade_date='2026-09-30', latest_date='2026-09-29')
    ingest(gate, data)
    record = next(r for r in gate._evidence if r.field == 'context.holdings[0].market_value_ars')
    assert record.as_of == data['context']['as_of']
    assert record.date == '2026-10-01'
    assert record.trade_date == record.timestamp == '2026-09-30'
    assert record.temporal_context == {'latest_date': '2026-09-29'}
    encoded = json.loads(json.dumps(asdict(record)))
    assert asdict(EvidenceRecord(**encoded)) == asdict(record)
    for field in ['snapshot_id', 'as_of', 'date', 'trade_date', 'temporal_context']:
        del encoded[field]
    old = EvidenceRecord(**encoded)
    assert old.as_of is None and old.snapshot_id is None
    assert old.date is None and old.trade_date is None and old.temporal_context is None
    assert old.timestamp == record.timestamp
    historical = payload()
    del historical['context']['as_of']
    del historical['context']['snapshot_id']
    ingest(gate, historical, 'historical-call')
    assert all(r.as_of is None and r.snapshot_id is None for r in gate._evidence if r.call_id == 'historical-call')


def test_partial_as_of_is_fail_closed_even_if_date_matches(tmp_path):
    data = payload()
    data['context']['date'] = '2026-10-01'
    other = copy.deepcopy(data)
    del other['context']['as_of']
    result = validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                      G + '; ' + ref('totals.portfolio_market_value_ars', 'other-call'),
                      6615045 / TOTAL * 100, data=data, other=other)
    assert not result.valid
    assert any(i.get('reason') == 'operand_snapshot_unavailable' for i in result.issues)


def test_equivalent_as_of_instants_are_canonicalized(tmp_path):
    other = payload()
    other['context']['as_of'] = '2026-10-07T04:31:01+01:00'
    assert validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                    G + '; ' + ref('totals.portfolio_market_value_ars', 'other-call'),
                    6615045 / TOTAL * 100, other=other).valid


@pytest.mark.parametrize('key', ['date', 'trade_date'])
def test_same_call_nested_as_of_conflict_cannot_be_hidden(tmp_path, key):
    data = payload()
    data['context'][key] = '2026-10-01'
    data['context']['totals']['as_of'] = '2026-10-08T03:31:01+00:00'
    result = validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                      G + '; ' + T, 6615045 / TOTAL * 100, data=data)
    assert not result.valid
    assert any(i.get('reason') == 'operand_snapshot_conflict' for i in result.issues)


@pytest.mark.parametrize('expr', [
    '6615045 * 100 * 100 / 222105473.58200002 * 100',
    '6615045 / 0.0001 / 222105473.58200002 * 100',
])
def test_scalar_reassociation_cannot_evade_effective_scale_cap(tmp_path, expr):
    result = validate(tmp_path, expr, G + '; ' + T, eval(expr, {'__builtins__': {}}, {}))
    assert not result.valid, result.issues
    assert any(i.get('reason') == 'scalar_subtree_out_of_range' for i in result.issues)


def test_explicit_missing_snapshot_is_not_filled_from_parent(tmp_path):
    data = payload()
    data['context']['totals']['snapshot_id'] = None
    result = validate(tmp_path, '6615045 / 222105473.58200002 * 100',
                      G + '; ' + T, 6615045 / TOTAL * 100, data=data)
    assert not result.valid
    assert any(i.get('reason') == 'operand_snapshot_unavailable' for i in result.issues)


def _balanced_sum(leaves):
    if len(leaves) == 1:
        return leaves[0]
    middle = len(leaves) // 2
    return '(' + _balanced_sum(leaves[:middle]) + ' + ' + _balanced_sum(leaves[middle:]) + ')'


@pytest.mark.parametrize('numerator', [
    '6615045*100 + 6615045*100',
    ' + '.join(['6615045'] * 100),
    _balanced_sum(['6615045'] * 128),
    '(' * 60 + '6615045' + ' +6615045)' * 60,
    '6615045*100 - (-(6615045*100))',
    '(6615045+6615045)*100',
    '(6615045+6615045)/0.01',
])
def test_distributed_coefficients_cannot_evade_cap(tmp_path, numerator):
    expr = '(' + numerator + ') / 222105473.58200002 *100'
    result = validate(tmp_path, expr, G + '; ' + T, eval(expr, {'__builtins__': {}}, {}))
    assert not result.valid, result.issues
    assert any(i.get('reason') == 'scalar_subtree_out_of_range' for i in result.issues)


@pytest.mark.parametrize('numerator', ['6615045*100 +6615045*100', '(6615045+6615045)*100'])
def test_coefficient_200_is_rejected_without_percent_conversion(tmp_path, numerator):
    expr = '(' + numerator + ') / 222105473.58200002'
    assert not validate(tmp_path, expr, G + '; ' + T, eval(expr, {'__builtins__': {}}, {})).valid


def test_same_ref_twice_accumulates_two_and_cancellation_is_algebraic(tmp_path):
    for expr in ['(6615045+6615045)/222105473.58200002',
                 '(6615045*100+6615045*100-6615045*100)/222105473.58200002']:
        result = validate(tmp_path, expr, G + '; ' + T, eval(expr, {'__builtins__': {}}, {}))
        assert result.valid, result.issues


@pytest.mark.parametrize('expr,expected', [
    ('(6615045+6615045)/222105473.58200002', [2]),
    ('(6615045*100+6615045*100)/222105473.58200002', [200]),
    ('(6615045+1384425)/222105473.58200002', [1, 1]),
    ('(6615045*100+6615045*100-6615045*100)/222105473.58200002', [100]),
])
def test_exact_coefficient_proof(tmp_path, expr, expected):
    from src.agent.grounding.derived import validate_operands
    from src.agent.grounding.policies import _formula_in_note

    gate = GroundingLedger(run_dir=tmp_path, user_message='Analyze portfolio')
    ingest(gate, payload())
    paths = {'context.holdings[0].market_value_ars', 'context.totals.portfolio_market_value_ars'}
    if '1384425' in expr:
        paths.add('context.holdings[1].market_value_ars')
    records = [r for r in gate._evidence if r.field in paths]
    proof = {}
    reason = validate_operands(_formula_in_note(expr)[2], records, None, coefficient_proof=proof)
    assert list(proof.values()) == expected
    assert reason == ('scalar_subtree_out_of_range' if max(expected) > 100 else None)
    assert len({key[0] for key in proof}) == len(expected)
    assert all(key[1][1] == 'context.totals.portfolio_market_value_ars' for key in proof)


def test_equal_values_distinct_refs_are_not_fused():
    from fractions import Fraction

    from src.agent.grounding.derived import _merge_coefficients

    # Identity is entirely source-based; equal numeric evidence is irrelevant
    # to coefficient merging. The end-to-end ambiguous-value guard remains.
    a = (('call', 'holdings[0].market_value_ars', None), None)
    b = (('call', 'holdings[1].market_value_ars', None), None)
    assert _merge_coefficients({a: Fraction(1)}, {b: Fraction(1)}) == {a: 1, b: 1}
    assert _merge_coefficients({a: Fraction(1)}, {a: Fraction(1)}) == {a: 2}


def test_equivalent_provider_ref_spellings_fuse_before_coefficients(tmp_path):
    gate = GroundingLedger(run_dir=tmp_path, user_message='Analyze GGAL portfolio')
    ingest(gate, payload(), 'call_one|fc_suffix')
    refs = ('call_one::context.holdings[0].market_value_ars; '
            'call_one|fc_suffix::context.holdings[0].market_value_ars; '
            'call_one::context.totals.portfolio_market_value_ars')
    expr = '(6615045+6615045)/222105473.58200002'
    answer = f'GGAL: {2*6615045/TOTAL:.6f} puntos porcentuales.\n\n```figures\n{2*6615045/TOTAL:.6f} | derived | {expr} | {refs}\n```'
    result = gate.validate_final_answer(answer)
    assert result.valid, result.issues
    expr = '(6615045*100+6615045*100)/222105473.58200002'
    answer = f'GGAL: {200*6615045/TOTAL:.6f} puntos porcentuales.\n\n```figures\n{200*6615045/TOTAL:.6f} | derived | {expr} | {refs}\n```'
    result = gate.validate_final_answer(answer)
    assert not result.valid
    assert any(i.get('reason') == 'scalar_subtree_out_of_range' for i in result.issues)


@pytest.mark.parametrize('left,right,include_bbar', [
    ('(6615045+1384425)/222105473.58200002*100',
     '(6615045/222105473.58200002+1384425/222105473.58200002)*100', True),
    ('6615045*0.50/222105473.58200002*100',
     '(6615045*0.25+6615045*0.25)/222105473.58200002*100', False),
    ('6615045*(1-0.25)/222105473.58200002*100',
     '(6615045-6615045*0.25)/222105473.58200002*100', False),
])
def test_algebraically_equivalent_forms_have_same_proof(tmp_path, left, right, include_bbar):
    import math

    from src.agent.grounding.derived import validate_operands
    from src.agent.grounding.policies import _formula_in_note

    gate = GroundingLedger(run_dir=tmp_path, user_message='Analyze portfolio')
    ingest(gate, payload())
    paths = {'context.holdings[0].market_value_ars', 'context.totals.portfolio_market_value_ars'}
    if include_bbar:
        paths.add('context.holdings[1].market_value_ars')
    records = [r for r in gate._evidence if r.field in paths]
    proofs = []
    values = []
    for expr in [left, right]:
        value, _, tree = _formula_in_note(expr)
        proof = {}
        assert validate_operands(tree, records, None, coefficient_proof=proof) is None
        proofs.append(proof)
        values.append(value)
    assert proofs[0] == proofs[1]
    assert math.isclose(values[0], values[1], rel_tol=1e-12)


@pytest.fixture
def generic_operand_gate(tmp_path):
    gate = GroundingLedger(run_dir=tmp_path, user_message='Analyze generic observations')
    data = {'snapshot_id': 'one', 'as_of': '2026-10-07T00:00:00Z', 'currency': 'USD',
            'positions': [{'symbol': 'AAA.US', 'market_value': 7},
                          {'symbol': 'BBB.US', 'market_value': 11}],
            'totals': {'market_value': 1000}}
    gate.ingest_tool_result(tool_name='generic_snapshot', arguments={},
                           result=json.dumps(data), call_id='call', success=True)
    return gate


def _generic_proof(gate, expr, paths=('positions[0].market_value',)):
    from src.agent.grounding.derived import validate_operands
    from src.agent.grounding.policies import _formula_in_note

    value, _, tree = _formula_in_note(expr)
    records = [r for r in gate._evidence if r.field in paths]
    proof = {}
    reason = validate_operands(tree, records, None, coefficient_proof=proof)
    refs = '; '.join('call::' + r.field for r in records)
    result = gate.validate_final_answer(
        f'Portfolio: {value:.6f}.\n\n```figures\n{value:.6f} | derived | {expr} | {refs}\n```')
    return proof, reason, result


@pytest.mark.parametrize('forms,coefficient', [
    (['7*2', '7+7', '7-(-7)', '(7*4)/2', '((+7)*(+4))/(+2)',
      '(7+7)+(7-7)', '((7+7)+7)-7'], 2),
    (['7*1', '7*100-7*99', '7*100-7*100*.99', '7*(1-.99)*100',
      '-(-(7*100-7*99))', '7*(200-199)', '(7*400-7*396)/4'], 1),
    (['7*100', '7*(1-.99)*100*100', '(7*400)/4',
      '7/(1/100)', '(7*200)-(7*100)'], 100),
])
def test_generic_equivalence_has_identical_normalized_map(generic_operand_gate, forms, coefficient):
    from fractions import Fraction

    expected = {(('call', 'positions[0].market_value', None), None): Fraction(coefficient)}
    for expr in forms:
        proof, reason, result = _generic_proof(generic_operand_gate, expr)
        assert proof == expected, (expr, proof)
        assert reason is None, (expr, reason)
        assert result.valid, (expr, result.issues)


@pytest.mark.parametrize('expr', [
    '7*200', '7*100+7*100', '(7*400)/2', '7/0.005',
    _balanced_sum(['7'] * 200),
    ' + '.join(['7'] * 200),
    '7*(-200)', '7*(1/(1/200))',
])
def test_generic_excessive_equivalent_scales_have_same_proof(generic_operand_gate, expr):
    from fractions import Fraction

    proof, reason, result = _generic_proof(generic_operand_gate, expr)
    assert len(proof) == 1
    assert abs(next(iter(proof.values()))) == Fraction(200)
    assert reason == 'scalar_subtree_out_of_range'
    assert not result.valid


@pytest.mark.parametrize('expr', ['7*0.10000000000000000000001*1000',
                                  '7*(1-.99999999999999999999999)*1.00000000000000000000001e25'])
def test_decimal_text_not_float_roundtrip_controls_boundary(generic_operand_gate, expr):
    proof, reason, result = _generic_proof(generic_operand_gate, expr)
    assert next(iter(proof.values())) > 100
    assert reason == 'scalar_subtree_out_of_range'
    assert not result.valid


@pytest.mark.parametrize('expr', ['7/1000', '7/(100*100)', '7/(400/2)',
                                  '(7+10000)', '(100*100)', '(7+999)-(999)',
                                  '7/(1000-1000)', '7**2'])
def test_generic_unobserved_amounts_and_unsupported_forms_still_fail(generic_operand_gate, expr):
    from src.agent.grounding.policies import _formula_in_note

    if _formula_in_note(expr) is None:
        return  # Zero denominator is rejected by the existing safe evaluator.
    _, reason, result = _generic_proof(generic_operand_gate, expr)
    assert reason is not None
    assert not result.valid


def test_generic_distinct_refs_and_common_denominator_survive(generic_operand_gate):
    paths = ('positions[0].market_value', 'positions[1].market_value', 'totals.market_value')
    proofs = []
    for expr in ['(7+11)/1000*100', '(7/1000+11/1000)*100',
                 '((7+11)*400)/4/1000']:
        proof, reason, result = _generic_proof(generic_operand_gate, expr, paths)
        assert reason is None
        assert result.valid
        assert len(proof) == 2
        assert set(proof.values()) == {100}
        assert all(key[1] == ('call', 'totals.market_value', None) for key in proof)
        proofs.append(proof)
    assert proofs[0] == proofs[1] == proofs[2]


def test_identity_leaf_has_same_map_as_coefficient_one(generic_operand_gate):
    import ast

    from src.agent.grounding.derived import validate_operands

    tree = ast.parse('7', mode='eval')
    tree.body._grounding_literal = '7'
    proof = {}
    records = [r for r in generic_operand_gate._evidence if r.field == 'positions[0].market_value']
    assert validate_operands(tree, records, None, coefficient_proof=proof) is None
    assert proof == _generic_proof(generic_operand_gate, '7*100-7*99')[0]
