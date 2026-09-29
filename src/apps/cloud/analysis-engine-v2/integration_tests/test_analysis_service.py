"""Verify real tool execution and screen assembly around a deterministic model fake."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.analysis_service import execute_request
from edge_analysis_v2.fixture_tools import make_fixture
from edge_analysis_v2.factor_store import HEADLINES, read_factor_details


@pytest.fixture
def run_context(tmp_path):
    dsn=os.environ['V2_FACTOR_TEST_DSN']
    config=conninfo_to_dict(dsn)
    assert config.get('host')=='127.0.0.1' and config.get('port')=='55440' and config.get('dbname')=='analysis_v2'
    identities=[]
    def factory():
        return psycopg.connect(dsn,autocommit=True)
    with factory() as conn:
        before = {r[0] for r in conn.execute('SELECT tool_id FROM tool_definitions')}
    def request(kind, model_call, fixture=None, identity=None):
        identity=identity or 'service-test-'+uuid4().hex
        if identity not in identities:
            identities.append(identity)
        fixture=fixture or make_fixture()
        return execute_request(kind=kind,fixture=fixture,connection_factory=factory,key='fake-key',
            artifacts=tmp_path/identity,analysis_id=identity,model_call=model_call), identity
    yield request,factory
    with factory() as conn:
        for identity in reversed(identities):
            conn.execute('DELETE FROM tool_runs WHERE movement_analysis_id=%s OR outlook_analysis_id=%s',(identity,identity))
            for table in ('movement_items','outlook_items','outlook_factors','outlook_conclusion_keywords','outlook_factor_metrics','outlook_issue_items','movement_analyses','outlook_analyses'):
                conn.execute('DELETE FROM '+table+' WHERE analysis_id=%s',(identity,))
        created = {r[0] for r in conn.execute('SELECT tool_id FROM tool_definitions')} - before
        for tool_id in created:
            conn.execute('DELETE FROM tool_definitions WHERE tool_id=%s AND NOT EXISTS (SELECT 1 FROM tool_runs WHERE tool_id=%s)',(tool_id,tool_id))


def news_reference(kwargs):
    return kwargs['call']('get_issue_evidence',{'news_ids':[kwargs['initial']['news'][0]['news_id']], 'include_body':False})['tool_run_id']


def test_movement_calls_commit_before_final_publication_and_retry_does_not_call_model(run_context):
    request, factory=run_context
    calls=[]
    async def model(**kwargs):
        calls.append(True)
        reference=news_reference(kwargs)
        with factory() as connection:
            assert connection.execute('SELECT status FROM tool_runs WHERE tool_run_id=%s',(reference,)).fetchone()==('completed',)
        return {'new_items':[dict(candidate_id='new',type='이슈',title_keyword='신규 계약',sentence='공급 물량을 확보했어요.',sentiment='positive',tool_run_ids=[reference])],
                'selected_item_ids':['new'],'summary':'공급 물량을 확보했어요.'}
    result,identity=request('movement',model)
    assert result['items'][0]['sentence']=='공급 물량을 확보했어요.'
    repeated,_=request('movement',model,identity=identity)
    assert repeated==result and len(calls)==1


def test_model_failure_keeps_committed_evidence_and_never_publishes(run_context):
    request,factory=run_context
    identity='service-test-'+uuid4().hex
    async def model(**kwargs):
        news_reference(kwargs)
        raise ValueError('invalid final response')
    with pytest.raises(ValueError):
        request('movement',model,identity=identity)
    with factory() as connection:
        assert connection.execute('SELECT status FROM movement_analyses WHERE analysis_id=%s',(identity,)).fetchone()==('failed',)
        assert connection.execute('SELECT count(*) FROM tool_runs WHERE movement_analysis_id=%s',(identity,)).fetchone()==(1,)
        assert connection.execute('SELECT count(*) FROM movement_items WHERE analysis_id=%s',(identity,)).fetchone()==(0,)


def test_outlook_body_and_independent_features_publish_with_factor_cards(run_context):
    request,factory=run_context
    async def model(**kwargs):
        reference=news_reference(kwargs)
        for factor in ('차트','매크로','밸류','수급'):
            kwargs['call']('get_factor_metrics',{'type':factor})
        kwargs['call']('write_outlook_body',{'title':'물량 확대를 확인해요','items':[
            dict(id='supply',title_keyword='공급 확대',sentences=['추가 공급 계약을 확보했어요.'],tool_run_ids=[reference])]})
        return {'outlook':{'direction':'상승'},'summary_card':{'title':'공급 확대','summary':'계약 이행을 확인해요.'},
            'factors':[{'type':f,'sticker':'중립','sentence':'기간별 관측값을 확인했어요.'} for f in ('이슈','차트','매크로','밸류','수급')],
            'conclusion':{'title':'계약 이행 확인','supports':[{'label':'계약','tool_run_ids':[reference]}],'burdens':[],'sentence':'공급 이행을 지켜봐요.'},
            'issue_detail':{'headline':'공급 일정 확인','items':[dict(title_keyword='공급 계약',sentence='물량을 확보했어요.',sentiment='positive',tool_run_ids=[reference])]}}
    result,identity=request('outlook',model)
    assert set(result)=={'outlook','summary_card','detail','factors','conclusion'}
    assert result['detail']['items'][0]['sentences'][0]['is_updated'] is False
    with factory() as connection:
        assert connection.execute('SELECT count(*) FROM outlook_factor_metrics WHERE analysis_id=%s',(identity,)).fetchone()[0]>10
        assert connection.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s',(identity,)).fetchone()==('completed',)
        screens = read_factor_details(connection, identity)
        assert set(screens) == {'이슈', '차트', '매크로', '밸류', '수급'}
        for factor in result['factors']:
            screen = screens[factor['type']]
            assert screen['sticker'] == factor['sticker']
            if factor['type'] == '이슈':
                assert set(screen) == {'type', 'sticker', 'headline', 'items'}
            else:
                assert set(screen) == {'type', 'sticker', 'headline', 'analysis_at', 'metrics'}
                assert screen['headline'] == HEADLINES[factor['sticker']]
                assert screen['metrics']
                for metric in screen['metrics']:
                    assert {'key', 'value', 'observed_at'} <= set(metric)
                    assert set(metric) <= {'key', 'value', 'observed_at', 'subject'}
