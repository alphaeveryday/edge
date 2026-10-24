"""A long-window sale must not hide a recent reversal from the model's first input."""
import pytest

from edge_analysis_v2.analysis.current_flow import current_flow
from edge_analysis_v2.agent.work_context import WorkContext
from edge_analysis_v2.tools.execution import ToolExecutionError


def test_both_windows_and_investors_arrive_without_a_model_tool_decision():
    calls = []
    def call(name, arguments):
        calls.append((name, arguments))
        return {'result': arguments | {'amount_krw': 550 if arguments['lookback_days'] == 5 else -598},
                'tool_run_id': f'f{len(calls)}'}
    value = current_flow(call)
    initial = WorkContext({'current_flow': value}).initial
    assert initial['current_flow'] == value
    assert len(calls) == 4
    assert {(a['investor'], a['lookback_days']) for _, a in calls} == {
        ('foreign', 5), ('foreign', 20), ('institution', 5), ('institution', 20)}
    assert [x['result']['amount_krw'] for x in value[:2]] == [550, -598]
    assert all('tool_run_id' in x for x in value)


def test_unavailable_window_is_visible_not_zero_or_silently_omitted():
    def call(name, arguments):
        if arguments['lookback_days'] == 20:
            raise ToolExecutionError('a' * 32, 'insufficient observations')
        return {'result': arguments | {'amount_krw': 3}, 'tool_run_id': 'valid-id'}
    value = current_flow(call)
    assert len(value) == 4
    for item in value[1::2]:
        assert item['status'] == 'unavailable'
        assert 'amount_krw' not in item and 'tool_run_id' not in item
        assert item['error'] == 'insufficient observations'
        assert 'a' * 32 not in str(item)


def test_persistence_failure_is_not_disguised_as_missing_market_data():
    def call(*args):
        raise ConnectionError('database')
    with pytest.raises(ConnectionError):
        current_flow(call)
