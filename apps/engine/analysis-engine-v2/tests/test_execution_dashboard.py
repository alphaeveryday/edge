"""Keep local agent execution bounded and credentials off the browser surface."""

from threading import Event
import json

import pytest

from edge_analysis_v2.dashboard.jobs import ExecutionDashboard, read_settings


def test_prompt_is_frozen_at_job_start_even_when_edited_before_model_call(tmp_path):
    from edge_analysis_v2.prompts.versions import PromptVersions
    source = tmp_path/'prompts'
    source.mkdir()
    (source/'movement.yaml').write_text('system_prompt: original', encoding='utf-8')
    prompts = PromptVersions(source, tmp_path/'history')
    entered, resume = Event(), Event()
    calls = []
    def fixture(*args, **kwargs):
        entered.set()
        assert resume.wait(5)
        return {}
    manager = ExecutionDashboard(tmp_path/'runs', key='secret', model='test', connection_factory=lambda:None,
        runner=lambda **kwargs:calls.append(kwargs['system_prompt']), fixture_factory=fixture, prompt_versions=prompts)
    job = manager.start({'kind':'movement','scenario':'baseline'})
    try:
        assert entered.wait(3)
        prompts.save('movement','system_prompt: changed',job['prompt_version'],'edit during execution')
    finally:
        resume.set()
        manager.worker.join(5)
    assert calls == ['original']
    saved = manager.detail(job['analysis_id'])
    assert saved['artifacts']['system_prompt.yaml'] == 'system_prompt: original'
    assert json.loads(saved['artifacts']['prompt_version.json'])['version'] == job['prompt_version']


@pytest.mark.parametrize('audit_status', ['passed', 'failed'])
def test_every_completed_run_audits_without_changing_execution_success(tmp_path, audit_status):
    calls = []
    def audit(kind, identity):
        calls.append((kind, identity))
        return {'status': audit_status, 'entries': []}
    manager = ExecutionDashboard(tmp_path, key='secret', model='test', connection_factory=lambda:None,
        runner=lambda **kwargs: {}, fixture_factory=lambda *args, **kwargs: {}, auditor=audit)
    job = manager.start({'kind':'movement', 'scenario':'baseline'})
    manager.worker.join(3)
    detail = manager.detail(job['analysis_id'])
    assert calls == [('movement', job['analysis_id'])]
    assert detail['job']['status'] == 'completed'
    assert detail['job']['contract_status'] == audit_status
    assert json.loads(detail['artifacts']['contract_audit.json'])['status'] == audit_status


def test_audit_outage_never_reexecutes_model_or_claims_pass(tmp_path):
    calls = []
    def audit(*args):
        raise RuntimeError('secret')
    manager = ExecutionDashboard(tmp_path, key='secret', model='test', connection_factory=lambda:None,
        runner=lambda **kwargs: calls.append(1), fixture_factory=lambda *args, **kwargs: {}, auditor=audit)
    job = manager.start({'kind':'outlook', 'scenario':'baseline'})
    manager.worker.join(3)
    detail = manager.detail(job['analysis_id'])
    assert calls == [1]
    assert detail['job']['status'] == 'completed'
    assert detail['job']['contract_status'] == 'not_checked'
    assert 'secret' not in str(detail)


def test_old_executions_remain_accessible_after_fifty_runs(tmp_path):
    manager = ExecutionDashboard(tmp_path, key='secret', model='test', connection_factory=lambda:None)
    for index in range(51):
        manager._save({'analysis_id':f'{index:032x}', 'started_at':str(index), 'status':'failed'})
    assert len(manager.jobs()) == 51


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


def test_independent_cases_never_inherit_another_case_but_replay_links_in_order(tmp_path):
    calls = []
    manager = ExecutionDashboard(tmp_path, key='secret', model='test', connection_factory=lambda:None,
        runner=lambda **kwargs:calls.append(kwargs), fixture_factory=lambda scenario,analysis_at:{})
    with pytest.raises(ValueError, match='preceding'):
        manager.start({'kind':'movement','scenario':'replay_2'})
    first = manager.start({'kind':'movement','scenario':'replay_1'})
    manager.worker.join(3)
    manager.start({'kind':'movement','scenario':'baseline'})
    manager.worker.join(3)
    assert calls[-1]['previous_analysis_id'] is None
    manager.start({'kind':'movement','scenario':'replay_2'})
    manager.worker.join(3)
    assert calls[-1]['previous_analysis_id'] == first['analysis_id']
    with pytest.raises(ValueError, match='preceding'):
        manager.start({'kind':'outlook','scenario':'replay_2'})
