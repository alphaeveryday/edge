"""Keep local agent execution bounded and credentials off the browser surface."""

from threading import Event
import json

import pytest

from edge_analysis_v2.execution_dashboard import ExecutionDashboard, read_settings


def test_only_whitelisted_environment_settings_are_loaded(tmp_path):
    path = tmp_path / '.env'
    path.write_text('DEEPSEEK_API_KEY="secret"\nDEEPSEEK_MODEL=deepseek-flash\nAWS_SECRET=hidden\n', encoding='utf-8')
    assert read_settings(path) == {'key':'secret', 'model':'deepseek-flash'}


def test_invalid_scenario_cannot_start_a_runner(tmp_path):
    manager = ExecutionDashboard(tmp_path, key='secret', model='test', connection_factory=lambda:None, runner=lambda **kwargs:None)
    with pytest.raises(ValueError):
        manager.start({'kind':'movement','scenario':'../../secrets'})
    assert manager.jobs() == []


def test_one_job_at_a_time_and_key_is_not_in_status(tmp_path):
    entered, finish = Event(), Event()
    def runner(**kwargs):
        assert kwargs['key'] == 'secret'
        entered.set()
        finish.wait(3)
        return {'summary':'done', 'items':[]}
    manager = ExecutionDashboard(tmp_path, key='secret', model='test', connection_factory=lambda:None,
                                 runner=runner, fixture_factory=lambda scenario,analysis_at:{'context':{'analysis_at':analysis_at}})
    first = manager.start({'kind':'movement','scenario':'baseline'})
    assert entered.wait(2)
    try:
        with pytest.raises(ValueError, match='already'):
            manager.start({'kind':'outlook','scenario':'baseline'})
        assert 'secret' not in str(manager.detail(first['analysis_id']))
    finally:
        finish.set()
        manager.worker.join(3)
    assert manager.detail(first['analysis_id'])['job']['status'] == 'completed'


def test_job_errors_are_visible_without_secret_leakage(tmp_path):
    def runner(**kwargs):
        raise ValueError('bad key secret')
    manager = ExecutionDashboard(tmp_path, key='secret', model='test', connection_factory=lambda:None,
                                 runner=runner, fixture_factory=lambda scenario,analysis_at:{})
    job = manager.start({'kind':'movement','scenario':'followup'})
    manager.worker.join(3)
    detail = manager.detail(job['analysis_id'])
    assert detail['job']['status'] == 'failed'
    assert 'secret' not in str(detail)
    assert 'ValueError' in detail['job']['error']


def test_unknown_id_never_becomes_a_filesystem_path(tmp_path):
    manager = ExecutionDashboard(tmp_path, key='secret', model='test', connection_factory=lambda:None)
    assert manager.detail('../outside') is None


def test_restart_marks_previous_running_job_interrupted_without_reexecuting(tmp_path):
    identity = 'a'*32
    folder = tmp_path/identity
    folder.mkdir()
    (folder/'job.json').write_text(json.dumps({'analysis_id':identity,'status':'running','started_at':'2026-09-14'}),encoding='utf-8')
    manager = ExecutionDashboard(tmp_path,key='secret',model='test',connection_factory=lambda:None)
    assert manager.jobs()[0]['status'] == 'interrupted'
    assert manager.worker is None
