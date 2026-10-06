"""A failed hypothesis must say what the agent actually did, and must not be judged past its cause."""
from edge_analysis_v2.quality.tool_intent import check


def call(tool, arguments, identifier, error=None):
    return {'tool': tool, 'arguments': arguments, 'error': error, 'finished_at': identifier,
            'response': {'tool_run_id': identifier, 'result': {}}}


HYPOTHESES = [
    {'id': 'answer', 'kind': '답변 반영', 'claim': 'missing code stays visible', 'check': {'answer': {'contains': ['999999']}}},
    {'id': 'chain', 'kind': '연결', 'claim': 'prices reuse the selection',
     'check': {'passes_reference': {'tool': 'resolve', 'to': ['prices']}}},
    {'id': 'args', 'kind': '인자', 'claim': 'one batch on the Korean exchange',
     'check': {'first_arguments': {'tool': 'resolve', 'equals': {'market_code': 'XKRX', 'tickers': ['B', 'A']}}}},
    {'id': 'select', 'kind': '선택', 'claim': 'one batch call instead of one search per ticker',
     'check': {'count': {'tool': 'resolve', 'min': 1, 'max': 1}, 'errors': {'tool': 'resolve'}}},
    {'id': 'read', 'kind': '해석', 'claim': 'needs a reader'},
]
GOOD = [call('resolve', {'tickers': ['A', 'B'], 'market_code': 'XKRX'}, 'cq_1'),
        call('prices', {'targets': {'kind': 'selection_ref', 'ref': {'tool_run_id': 'cq_1', 'path': '/selection'}}}, 'cq_2')]


def statuses(calls, answer='999999 미확인'):
    return {v['id']: v['status'] for v in check(HYPOTHESES, calls, {'answer': answer})}


def test_a_run_that_behaves_as_intended_passes_and_leaves_reader_checks_open():
    assert statuses(GOOD) == {'select': 'pass', 'args': 'pass', 'read': 'manual', 'chain': 'pass', 'answer': 'pass'}


def test_a_wrong_choice_blocks_later_hypotheses_instead_of_failing_them_too():
    verdicts = check(HYPOTHESES, [call('search', {'query': 'A'}, 'cq_1'), call('search', {'query': 'B'}, 'cq_2')], {'answer': ''})
    assert [v['status'] for v in verdicts] == ['fail', 'blocked', 'blocked', 'blocked', 'blocked']
    assert verdicts[0]['observed']['count'] == {'calls': 0, 'order': ['search', 'search']}
    assert verdicts[1]['observed'] == {'failed_first': 'select'}


def test_retyped_identifiers_fail_the_chain_and_show_the_arguments_used():
    retyped = [GOOD[0], call('prices', {'targets': {'kind': 'objects', 'object_refs': [{'object_id': 'a'}]}}, 'cq_2')]
    verdict = next(v for v in check(HYPOTHESES, retyped, {'answer': '999999'}) if v['id'] == 'chain')
    assert verdict['status'] == 'fail' and verdict['observed']['passes_reference']['next'] == 'prices'


def test_split_calls_wrong_market_and_tool_errors_are_each_caught():
    split = [call('resolve', {'tickers': ['A'], 'market_code': 'XKRX'}, 'cq_1'), call('resolve', {'tickers': ['B'], 'market_code': 'XKRX'}, 'cq_2')]
    assert statuses(split)['select'] == 'fail'
    assert statuses([call('resolve', {'tickers': ['A', 'B'], 'market_code': 'KRX'}, 'cq_1')])['args'] == 'fail'
    assert statuses([call('resolve', {'tickers': ['A', 'B'], 'market_code': 'XKRX'}, 'cq_1', error='bad')])['select'] == 'fail'


def test_a_dropped_fact_in_the_answer_fails_only_the_answer_hypothesis():
    assert statuses(GOOD, answer='모두 확인했습니다') == {
        'select': 'pass', 'args': 'pass', 'read': 'manual', 'chain': 'pass', 'answer': 'fail'}


def test_identical_repeated_calls_are_reported_with_their_arguments():
    rows = [{'id': 'stop', 'kind': '비호출', 'claim': 'no repeated call', 'check': {'no_repeat': {'tool': 'resolve'}}}]
    verdict = check(rows, [GOOD[0], call('resolve', {'market_code': 'XKRX', 'tickers': ['A', 'B']}, 'cq_3')], {})[0]
    assert verdict['status'] == 'fail' and len(verdict['observed']['no_repeat']['repeated_arguments']) == 1
