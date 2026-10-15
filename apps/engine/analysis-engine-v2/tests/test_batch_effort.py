"""Only tomorrow's DeepSeek outlook batches lower effort; manual runs and old replays retain it."""
import json
import pytest

from edge_analysis_v2.cloud.contract import decode_request
from edge_analysis_v2.cloud.worker import batch_effort


BASE = {'analysis_id': 'a'*32, 'kind': 'outlook', 'etf_code': '102960',
        'analysis_at': '2026-10-15T21:00:00Z', 'execution_mode': 'batch'}


@pytest.mark.parametrize('changes,model,expected', [
    ({}, 'deepseek-flash', 'low'),
    ({'analysis_at': '2026-10-15T15:00:00Z'}, 'deepseek-flash', 'low'),
    ({'analysis_at': '2026-10-15T14:59:59Z'}, 'deepseek-flash', 'high'),
    ({'analysis_at': '2026-10-16T06:00:00+09:00'}, 'deepseek-flash', 'low'),
    ({'execution_mode': None}, 'deepseek-flash', 'high'),
    ({'kind': 'movement'}, 'deepseek-flash', 'high'),
    ({}, 'other-model', 'high'),
])
def test_effort_uses_request_cutoff_and_explicit_batch_identity(changes, model, expected):
    assert batch_effort(BASE | changes, model) == expected


def test_batch_marker_is_internal_and_only_for_outlook():
    assert decode_request(json.dumps(BASE), internal=True) == BASE
    with pytest.raises(ValueError):
        decode_request(json.dumps(BASE))
    for changes in ({'execution_mode': 'manual'}, {'kind': 'movement'}):
        with pytest.raises(ValueError):
            decode_request(json.dumps(BASE | changes), internal=True)
