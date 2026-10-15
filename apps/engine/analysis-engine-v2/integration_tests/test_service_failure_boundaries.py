"""Audit failures cannot change a draft; display failures cannot undo publication."""

import os
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.analysis import service as analysis_service
from edge_analysis_v2.tools.fixture_data import make_fixture
from edge_analysis_v2.storage.tool_runs import ToolStore
from edge_analysis_v2.tools.execution import ToolExecutionError


@pytest.fixture
def execution(tmp_path):
    dsn = os.environ['V2_FACTOR_TEST_DSN']
    settings = conninfo_to_dict(dsn)
    if (settings.get('host') != '127.0.0.1' or settings.get('port') != '55440'
            or settings.get('dbname') != 'analysis_v2'):
        raise ValueError('Dedicated local factor test database required')
    identity = 'service-boundary-' + uuid4().hex
    def factory():
        return psycopg.connect(dsn, autocommit=True)
    with factory() as connection:
        before = {row[0] for row in connection.execute('SELECT tool_id FROM tool_definitions')}
    def execute(model):
        return analysis_service.execute_request(kind='outlook', fixture=make_fixture(),
            connection_factory=factory, key='fake-key', artifacts=tmp_path/identity,
            analysis_id=identity, model_call=model)
    try:
        yield execute, factory, identity
    finally:
        with factory() as connection:
            connection.execute('DELETE FROM tool_runs WHERE outlook_analysis_id=%s', (identity,))
            for table in ('outlook_issue_items', 'outlook_factor_metrics', 'outlook_items',
                          'outlook_factors', 'outlook_conclusion_keywords', 'outlook_analyses'):
                connection.execute('DELETE FROM '+table+' WHERE analysis_id=%s', (identity,))
            created = {row[0] for row in connection.execute('SELECT tool_id FROM tool_definitions')} - before
            for tool_id in created:
                connection.execute('DELETE FROM tool_definitions WHERE tool_id=%s AND NOT EXISTS (SELECT FROM tool_runs WHERE tool_id=%s)', (tool_id, tool_id))


def write_body(kwargs):
    reference = kwargs['call']('get_issue_evidence',
        {'news_ids': [kwargs['initial']['news'][0]['news_id']], 'include_body': False})['tool_run_id']
    kwargs['call']('write_outlook_body', {'title': '공급 계약 확인', 'items': [
        {'id': 'supply', 'title_keyword': '계약 물량', 'sentences': ['계약 물량을 확보했어요.'], 'sentiment': 'positive',
         'tool_run_ids': [reference]}]})
    return reference


def final_response(reference):
    return {'outlook': {'direction': '상승'}, 'summary_card': {'title': '계약 물량', 'summary': '공급 계획을 확인해요.'},
        'factors': [{'type': factor, 'sticker': '중립', 'sentence': '관측 자료를 확인했어요.'}
                    for factor in ('이슈', '차트', '매크로', '밸류', '수급')],
        'conclusion': {'title': '공급 계획', 'supports': [], 'burdens': [], 'sentence': '계약 이행을 확인해요.'},
        'issue_detail': {'headline': '공급 일정 확인', 'items': [
            {'title_keyword': '공급 계약', 'sentence': '물량을 확보했어요.',
             'sentiment': 'positive', 'tool_run_ids': [reference]}]}}


def test_failed_edit_audit_does_not_leak_into_published_body(execution, monkeypatch):
    execute, factory, identity = execution
    save_run = ToolStore.save_run
    def reject_edit(self, **arguments):
        if arguments['tool_id'] == 'apply_outlook_body_changes:v2':
            raise psycopg.OperationalError('Simulated audit write failure')
        return save_run(self, **arguments)
    monkeypatch.setattr(ToolStore, 'save_run', reject_edit)
    async def model(**kwargs):
        reference = write_body(kwargs)
        with pytest.raises(psycopg.OperationalError, match='audit write failure'):
            kwargs['call']('apply_outlook_body_changes', {'changes': [
                {'action': 'update', 'id': 'supply', 'sentences': ['감사 저장에 실패한 수정이에요.'], 'sentiment': 'positive',
                 'tool_run_ids': [reference], 'updated_sentence_numbers': [1]}]})
        return final_response(reference)
    screen = execute(model)
    assert screen['detail']['items'][0]['sentences'] == [
        {'sentence': '계약 물량을 확보했어요.', 'is_updated': False}]
    with factory() as connection:
        edits = connection.execute('''SELECT d.function_name FROM tool_runs r JOIN tool_definitions d USING(tool_id)
            WHERE r.outlook_analysis_id=%s AND d.function_name IN ('write_outlook_body','apply_outlook_body_changes')''', (identity,)).fetchall()
        assert edits == [('write_outlook_body',)]
        assert connection.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s', (identity,)).fetchone()[0] == 'completed'


def test_postcommit_display_read_failure_returns_completed_publication(execution, monkeypatch):
    execute, factory, identity = execution
    def unavailable(*args):
        raise psycopg.OperationalError('Simulated display read failure')
    monkeypatch.setattr(analysis_service, 'read_factor_details', unavailable)
    async def model(**kwargs):
        return final_response(write_body(kwargs))
    with pytest.warns(RuntimeWarning, match='Publication committed'):
        screen = execute(model)
    with factory() as connection:
        assert connection.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s', (identity,)).fetchone()[0] == 'completed'
        assert connection.execute('SELECT count(*) FROM outlook_factor_metrics WHERE analysis_id=%s', (identity,)).fetchone()[0] > 0
    async def must_not_run(**kwargs):
        pytest.fail('A completed request must not call the model again')
    assert execute(must_not_run) == screen


@pytest.mark.parametrize('operation', ['write_outlook_body', 'apply_outlook_body_changes'])
def test_agent_can_correct_nonfinal_evidence_before_publication(execution, operation):
    execute, factory, identity = execution
    failed_runs = []
    async def model(**kwargs):
        reference = write_body(kwargs)
        search = kwargs['call']('search_news_threads', {})['tool_run_id']
        topic = {'id': 'supply', 'title_keyword': '수정 계약', 'sentences': ['수정된 계약 물량이에요.'], 'sentiment': 'positive',
                 'tool_run_ids': [search]}
        invalid = ({'title': '수정 본문', 'items': [topic]} if operation == 'write_outlook_body'
                   else {'changes': [{'action': 'update', **topic}]})
        with pytest.raises(ToolExecutionError) as error:
            kwargs['call'](operation, invalid)
        failed_runs.append(error.value.tool_run_id)
        assert search in str(error.value)
        assert 'search_news_threads' in str(error.value)
        assert 'include_body=false' in str(error.value)
        # A harmless readback edit proves the rejected draft was not applied.
        draft = kwargs['call']('apply_outlook_body_changes', {'changes': []})['result']
        assert draft['items'][0]['sentences'][0]['sentence'] == '계약 물량을 확보했어요.'
        topic['tool_run_ids'][:] = [reference]
        kwargs['call'](operation, invalid)
        return final_response(reference)
    screen = execute(model)
    assert screen['detail']['items'][0]['sentences'][0]['sentence'] == '수정된 계약 물량이에요.'
    with factory() as connection:
        row = connection.execute('SELECT status,output,error_message FROM tool_runs WHERE tool_run_id=%s',
                                 (failed_runs[0],)).fetchone()
        assert row[0] == 'failed' and row[1] is None
        assert 'search_news_threads' in row[2]
        assert connection.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s', (identity,)).fetchone()[0] == 'completed'
