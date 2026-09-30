"""A successful model response must not hide broken persistence or evidence."""
from contextlib import nullcontext

import pytest

from edge_analysis_v2.quality import audit as quality_audit


@pytest.fixture
def audit(monkeypatch, tmp_path):
    state = {'saved': {'title': 'Stored analysis'}, 'calls': []}

    class Tools:
        final_tool_names = {'sum'}
        schemas = [{'function': {'name': 'sum'}}]

        def __init__(self, fixture):
            pass

        def call(self, name, arguments):
            return {'result': {'amount': sum(arguments['values'])}}

    class Store:
        def __init__(self, *args, **kwargs):
            pass

        def get_outlook(self, identity):
            return state['saved']

    monkeypatch.setattr(quality_audit, 'FixtureTools', Tools)
    monkeypatch.setattr(quality_audit, 'PublicationStore', Store)
    monkeypatch.setattr(quality_audit, 'read_analysis_evidence',
                        lambda *args: {'tool_runs': state['calls']})

    def verify():
        return quality_audit.verify_execution(
            lambda: nullcontext(None), {}, 'outlook', 'analysis',
            {'title': 'Stored analysis'}, tmp_path)

    return state, verify


def test_matching_storage_never_automatically_certifies_prose(audit):
    _, verify = audit
    result = verify()
    assert result['mechanical_status'] == 'passed'
    assert result['semantic_status'] == 'pending_review'


def test_returned_screen_cannot_mask_a_different_committed_screen(audit):
    state, verify = audit
    state['saved'] = {'title': 'Wrong persisted analysis'}
    assert verify()['mechanical_status'] == 'failed'


@pytest.mark.parametrize('output', [
    {'tool_run_id': 'another-call', 'result': {'amount': 3}},
    {'tool_run_id': 'call-1', 'result': {'amount': 30}},
])
def test_wrong_evidence_identity_or_calculation_is_detected(audit, output):
    state, verify = audit
    state['calls'] = [{'status': 'completed', 'function_name': 'sum',
                       'tool_run_id': 'call-1', 'arguments': {'values': [1, 2]},
                       'output': output}]
    assert verify()['mechanical_status'] == 'failed'


def test_failed_calls_remain_visible_even_if_screen_was_saved(audit):
    state, verify = audit
    state['calls'] = [{'status': 'failed', 'function_name': 'sum',
                       'tool_run_id': 'failed-call'}]
    result = verify()
    assert result['mechanical_status'] == 'failed'
    assert result['failed_call_count'] == 1
