import json
import sys

import pytest
from fastapi.testclient import TestClient

from edge_portfolio import verification as v, store
from edge_portfolio.admin_app import app
from test_store import isolated_db


def cases():
    return json.loads(v.CASES.read_text(encoding='utf-8'))


def test_versions_retries_and_unrun_cases_remain_separate(isolated_db):
    v.register('old', 'old-commit', 'isolated_runtime', cases())
    v.register('new', 'new-commit', 'production_runtime', cases())
    first = v.begin_attempt('old', 1, 'first')
    assert v.finish_attempt('old', 1, first['attempt'], None, ['MODEL_TIMEOUT'], 600, 'failed')
    changed_input = {**cases()[0]['input'], 'question': 'Changed question'}
    second = v.begin_attempt('old', 1, 'retry', changed_input)
    assert second['attempt'] == 2
    v.finish_attempt('old', 1, 2, {'sections': {}}, [], 100)
    assert not v.finish_attempt('old', 1, 1, {}, [], 0), 'An old failure must not be overwritten by success'
    assert v.begin_attempt('old', 1, 'first')['state'] == 'failed', 'Import is idempotent'
    assert v.attempt_detail('old', 1, 2)['input'] == changed_input
    with pytest.raises(ValueError, match='INPUT_CONFLICT'):
        v.begin_attempt('old', 1, 'retry')
    with TestClient(app, base_url='http://localhost:18080') as client:
        old = client.get('/api/portfolio/admin/verification-runs/old').json()
        new = client.get('/api/portfolio/admin/verification-runs/new').json()
        assert len(old['cases']) == len(new['cases']) == 20
        assert [a['state'] for a in old['attempts']] == ['failed', 'succeeded']
        assert new['attempts'] == []
        assert client.get('/api/portfolio/admin/verification-runs/old/1/1').json()['failures'] == ['MODEL_TIMEOUT']
        assert client.get('/api/portfolio/admin/verification-runs/new/1/1').status_code == 404
        assert client.post('/api/portfolio/admin/verification-runs').status_code == 405
    assert store.read('old', 'visitor') is None, 'Evaluation does not consume customer job quotas'
    with pytest.raises(ValueError, match='CONFLICT'):
        v.register('old', 'new-commit', 'isolated_runtime', cases())
    with pytest.raises(ValueError, match='20_CASES'):
        v.register('partial', 'commit', 'isolated_runtime', cases()[:19])
    assert v.detail('partial') is None


def test_machine_checks_do_not_call_empty_or_invalid_recommendations_a_pass():
    opinion = {'sections': {'opinion': {'paragraphs': ['conclusion']}}}
    assert '추천 비중 없음' in v.check_report(cases()[0], opinion)
    report = {**opinion, 'recommendation': {'allocations': [{'instrument_id': 'US:QQQ', 'account': '일반 계좌', 'weight_pct': 90}]}}
    assert set(v.check_report(cases()[0], report)) == {'추천 비중 합계가 100% 아님'}
    # 8차: 글로 쓴 계좌·종목 조건이 부딪쳐도 보류하지 않는다. 추천이 없으면 실패다
    assert v.check_report(cases()[16], opinion) == ['추천 비중 없음']
    assert v.check_report(cases()[19], {**opinion, 'scenarios': {'rows': [{'annual_return_pct': '5', 'years': 10, 'future_krw': '93470527'}]}}) == []
    for rate in ('5.00', 5.0):
        assert v.check_report(cases()[19], {**opinion, 'scenarios': {'rows': [{'annual_return_pct': rate, 'years': 10, 'future_krw': '93470527.0'}]}}) == []
    assert v.check_report(cases()[19], {**opinion, 'scenarios': {'rows': [{'annual_return_pct': '5', 'years': 10, 'future_krw': '101615000'}]}})


def test_stopped_runner_is_visible_without_erasing_attempt(isolated_db):
    v.register('run', 'version', 'isolated_runtime', cases())
    v.begin_attempt('run', 1, 'initial')
    with store.connect() as db:
        db.execute("UPDATE portfolio_verification_attempts SET created_at=now()-interval '13 minutes'")
    assert v.detail('run')['attempts'][0]['interrupted']
    assert v.detail('run')['attempts'][0]['state'] == 'running'


def test_cli_registers_only_the_version_embedded_in_the_image(isolated_db, monkeypatch, tmp_path):
    manifest = tmp_path / 'verification_cases.json'
    manifest.write_text(json.dumps(cases()), encoding='utf-8')
    monkeypatch.setattr(v, 'CASES', manifest)
    version = 'a' * 40
    monkeypatch.setattr(sys, 'argv', ['verification', '--version', version, '--register-only'])
    with pytest.raises(SystemExit):
        v.main()
    manifest.with_name('deployment.json').write_text(json.dumps({'version': 'b' * 40}))
    with pytest.raises(SystemExit):
        v.main()
    manifest.with_name('deployment.json').write_text(json.dumps({'version': version}))
    v.main()
    assert len(v.detail('deploy-' + version)['cases']) == 20
    assert v.detail('deploy-' + version)['attempts'] == []


@pytest.mark.asyncio
async def test_runner_recovers_latest_interrupted_retry_without_losing_history(isolated_db, monkeypatch):
    from edge_portfolio import agent
    version = 'a' * 40
    run_id = 'deploy-' + version
    v.register(run_id, version, 'production_runtime', cases())
    for case in cases():
        a = v.begin_attempt(run_id, case['case'], 'initial')
        v.finish_attempt(run_id, case['case'], a['attempt'], {}, [], 1, 'failed' if case['case'] == 1 else 'succeeded')
    v.begin_attempt(run_id, 1, 'interrupted-retry')
    calls = []
    async def analyze(request, key, on_stage):
        calls.append(request)
        return {'recommendation': {'allocations': [{'instrument_id': 'US:VOO', 'account': '일반 계좌', 'weight_pct': 100}]}, 'sections': {'opinion': {'paragraphs': ['conclusion']}}}
    monkeypatch.setattr(agent, 'run_analysis', analyze)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'synthetic')
    await v.run(version)
    attempts = [a for a in v.detail(run_id)['attempts'] if a['case_id'] == 1]
    assert [a['state'] for a in attempts] == ['failed', 'failed', 'succeeded']
    assert attempts[1]['failures'] == ['RUNNER_INTERRUPTED']
    assert len(calls) == 1
    await v.run(version)
    assert len(calls) == 1, 'A completed passing case is not silently rerun'
