"""The control Lambda runs at 256 MB where building AWS clients is slow; warm calls must reuse them."""
from contextlib import contextmanager
from unittest.mock import Mock

from edge_analysis_v2.cloud import control


def test_warm_invocations_reuse_clients_and_report_where_time_went(monkeypatch):
    sessions = []
    def session():
        sessions.append(Mock())
        return sessions[-1]
    @contextmanager
    def connect(ca_path, *, session=None, cloud=False):
        yield Mock()
    monkeypatch.setattr(control, '_CLIENTS', {})
    monkeypatch.setattr(control.boto3, 'Session', session)
    monkeypatch.setattr(control, 'connect_results', connect)
    monkeypatch.setattr(control, 'control', lambda *args, **kwargs: {'acquired': True, 'started_by': 'x', 'held': []})
    for name, value in {'EXECUTION_ARN_PREFIX': 'arn:aws:states:r:1:execution:w:', 'RDS_CA_PATH': '/ca.pem',
                        'CLUSTER_ARN': 'cluster', 'ANALYSIS_SLOTS': '3', 'TASK_FAMILY': 'w'}.items():
        monkeypatch.setenv(name, value)
    event = {'execution_arn': 'arn:aws:states:r:1:execution:w:one', 'action': 'acquire', 'request_key': 'outlook:069500'}
    first, second = control.handler(event, None), control.handler(event, None)
    assert len(sessions) == 1
    assert first['acquired'] and set(second['timing_ms']) == {'clients_ms', 'connect_ms', 'control_ms'}
