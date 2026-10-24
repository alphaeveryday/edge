"""Two judges are merged conservatively, and what the agent did is counted from the stored calls."""
import json

import pytest

from edge_analysis_v2.quality.cq import judge, measure


def review(fingerprint, **statuses):
    return {'fingerprint': fingerprint, 'unnecessary_calls': [], 'checks': [
        {'id': key, 'status': status, 'reason': key + ' ' + status, 'answer_span': '문장', 'evidence_ids': ['cq_' + key]}
        for key, status in statuses.items()]}


def test_two_judges_pass_a_criterion_only_together_and_any_reasoned_fail_stands():
    merged, agreed = judge.merge(review('f', accuracy='pass', reasoning='pass', research='unknown', fulfillment='fail'),
                                 review('f', accuracy='pass', reasoning='fail', research='pass', fulfillment='fail'))
    assert {c['id']: c['status'] for c in merged['checks']} == {'accuracy': 'pass', 'reasoning': 'fail', 'research': 'unknown', 'fulfillment': 'fail'}
    assert agreed == 2 and '[평가자 2] reasoning fail' in merged['checks'][1]['reason'] and '평가자 불일치' in merged['checks'][2]['reason']
    with pytest.raises(ValueError, match='different content'):
        judge.merge(review('f', accuracy='pass'), review('g', accuracy='pass'))




def call(tool, total=None, arguments=None, error=None, complete=True, identifier='cq_x'):
    result = {} if total is None else {'total_rows': total, 'page': {'complete': complete}}
    return {'tool': tool, 'arguments': arguments or {}, 'error': error, 'response': {'tool_run_id': identifier, 'result': result}}


def test_followups_are_counted_from_the_order_of_stored_calls():
    calls = [call('find_similar_events', 400, {'q': 1}, complete=False, identifier='cq_a'),     # broad, later narrowed to a readable set
             call('find_similar_events', 40, {'q': 2}, complete=False, identifier='cq_b'),      # partial, never followed
             call('get_price_observations', 0, {'d': 1}), call('get_price_observations', 5, {'d': 2}),   # empty, then retried
             call('get_financial_observations', arguments={'m': 'x'}, error='wrong argument')]           # error, not retried
    s = measure.followups(calls)
    assert (s['broad'], s['broad_then_narrower'], s['broad_then_readable']) == (1, 1, 1)
    assert (s['partial'], s['followed'], s['paged']) == (2, 1, 0)
    assert (s['empty'], s['empty_retried'], s['errors'], s['errors_retried'], s['wrong_argument']) == (1, 1, 1, 0, 1)


def test_a_won_amount_in_the_answer_is_checked_against_every_number_the_tools_returned():
    returned = json.dumps({'netValInstitutionTotal': 1447000000, 'value': '2004376374000', 'text': '7兆 수주 도전, 1.9조원 투입'}, ensure_ascii=False)
    kinds = lambda text: [kind for kind, _ in measure.check_amounts(text, returned)]
    assert kinds('기관 +14.47억원, 14억 4,700만원, 누적 2조 44억원, 1.9조원, 7조 원') == ['matched']*5
    assert kinds('기관 +145억원') == ['magnitude']        # ten times the returned amount
    assert kinds('누적 2조 437억원') == ['unmatched']      # a dropped digit
    assert kinds('52,250원, 3만원') == []                  # prices are not 조/억 amounts

