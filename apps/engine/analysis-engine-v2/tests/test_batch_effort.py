"""The internal batch marker does not select reasoning effort."""
import json
import pytest

from edge_analysis_v2.cloud.contract import decode_request


BASE = {'analysis_id': 'a'*32, 'kind': 'outlook', 'etf_code': '102960',
        'analysis_at': '2026-10-15T21:00:00Z', 'execution_mode': 'batch'}


def test_batch_marker_is_internal_and_only_for_outlook():
    assert decode_request(json.dumps(BASE), internal=True) == BASE
    with pytest.raises(ValueError):
        decode_request(json.dumps(BASE))
    for changes in ({'execution_mode': 'manual'}, {'kind': 'movement'}):
        with pytest.raises(ValueError):
            decode_request(json.dumps(BASE | changes), internal=True)
