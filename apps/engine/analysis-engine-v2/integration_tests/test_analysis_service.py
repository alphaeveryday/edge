"""Verify real tool execution and screen assembly around a deterministic model fake."""

import os
import json
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.analysis.service import execute_request
from edge_analysis_v2.tools.fixture_data import make_fixture
from edge_analysis_v2.storage.factors import HEADLINES, read_factor_details


def test_revision_receives_uncited_research_and_keeps_state_after_no_change(run_context):
    from copy import deepcopy
    from datetime import datetime, timedelta
    from edge_analysis_v2.storage.research import load_research
    request, factory = run_context
    fixture = make_fixture()
    async def first(**kwargs):
        news_reference(kwargs)  # Consulted but not selected for publication: still research.
        return {'new_items': [], 'selected_item_ids': [], 'summary': None}
    _, identity = request('movement', first, fixture)
    with factory() as connection:
        state = load_research(connection, 'movement', identity).snapshot()
    assert any(m['definition']['function_name'] == 'get_issue_evidence' for m in state['materials'].values())
    assert state['gaps'] == []
    later = deepcopy(fixture)
    later['context']['analysis_at'] = (datetime.fromisoformat(fixture['context']['analysis_at']) + timedelta(minutes=1)).isoformat()
    async def revision(**kwargs):
        assert kwargs['initial']['research_state'] == state
        assert kwargs['initial']['previous_analysis'] is not None
        return {'new_items': [], 'selected_item_ids': [], 'summary': None}
    _, revised_id = request('movement', revision, later)
    with factory() as connection:
        after = load_research(connection, 'movement', revised_id).snapshot()
    assert state['materials'].items() <= after['materials'].items()


@pytest.mark.parametrize('empty_selection', [False, True])
def test_retirement_cannot_leave_published_movement_using_retired_evidence(run_context, empty_selection):
    from copy import deepcopy
    from datetime import datetime, timedelta
    from edge_analysis_v2.storage.research import load_research
    request, factory = run_context
    fixture = make_fixture()
    async def first(**kwargs):
        ref = news_reference(kwargs)
        return {'new_items': [dict(candidate_id='kept', type='이슈', title_keyword='계약',
            sentence='판매 물량을 확보했어요.', sentiment='positive', tool_run_ids=[ref])],
            'selected_item_ids': ['kept'], 'summary': '계약을 확인했어요.'}
    _, parent_id = request('movement', first, fixture)
    later = deepcopy(fixture)
    later['context']['analysis_at'] = (datetime.fromisoformat(fixture['context']['analysis_at']) + timedelta(minutes=1)).isoformat()
    identity = 'service-test-' + uuid4().hex
    async def revision(**kwargs):
        item = kwargs['initial']['previous_items'][0]
        (kwargs['artifacts'] / 'research_removals.json').write_text(json.dumps([
            {'id': item['tool_run_ids'][0], 'reason': 'superseded'}]), encoding='utf-8')
        return {'new_items': [], 'selected_item_ids': [] if empty_selection else [item['item_id']], 'summary': None}
    with pytest.raises(ValueError, match='Retired research is still cited'):
        request('movement', revision, later, identity=identity)
    with factory() as connection:
        with pytest.raises(ValueError, match='completed'):
            load_research(connection, 'movement', identity)
        assert load_research(connection, 'movement', parent_id).snapshot()['removed'] == {}


def test_legacy_recovery_includes_uncited_ancestor_research(run_context):
    from copy import deepcopy
    from datetime import datetime, timedelta
    from edge_analysis_v2.storage.research import load_research
    request, factory = run_context
    fixture = make_fixture()
    async def first(**kwargs):
        news_reference(kwargs)
        return {'new_items': [], 'selected_item_ids': [], 'summary': None}
    _, first_id = request('movement', first, fixture)
    with factory() as connection:
        original = load_research(connection, 'movement', first_id).snapshot()
        connection.execute("DELETE FROM tool_runs WHERE movement_analysis_id=%s AND tool_id='record_research_state:v1'", (first_id,))
    later = deepcopy(fixture)
    later['context']['analysis_at'] = (datetime.fromisoformat(fixture['context']['analysis_at']) + timedelta(minutes=1)).isoformat()
    async def second(**kwargs):
        return {'new_items': [], 'selected_item_ids': [], 'summary': None}
    _, second_id = request('movement', second, later)
    with factory() as connection:
        connection.execute("DELETE FROM tool_runs WHERE movement_analysis_id=%s AND tool_id='record_research_state:v1'", (second_id,))
        restored = load_research(connection, 'movement', second_id).snapshot()
    assert original['materials'].items() <= restored['materials'].items()
    assert len(restored['gaps']) == 2


@pytest.mark.parametrize('kind', ['movement', 'outlook'])
def test_contract_audit_reads_committed_assembly_and_detects_stored_regression(run_context, tmp_path, kind, monkeypatch):
    from hashlib import sha256
    from edge_analysis_v2.contracts import screen_validation
    """A correct model response alone cannot prove the saved screen contract."""
    from edge_analysis_v2.contracts.audit import read_contract_audit
    from edge_analysis_v2.dashboard.server import assemble_screen
    from edge_analysis_v2.contracts.screen_validation import contract_info
    request, factory = run_context
    async def model(**kwargs):
        reference = news_reference(kwargs)
        if kind == 'movement':
            return {'new_items': [dict(candidate_id='new', type='이슈', title_keyword='계약',
                    sentence='판매 물량을 확보했어요.', sentiment='positive', tool_run_ids=[reference])],
                    'selected_item_ids': ['new'], 'summary': '공급 계약을 확인해요.'}
        kwargs['call']('get_instrument_factors', {'instrument_id': kwargs['initial']['context']['etf_code']})
        kwargs['call']('write_outlook_body', {'title': '계약 이행을 확인해요', 'items': [
            dict(id='contract', title_keyword='계약', sentences=['판매 물량을 확보했어요.'], sentiment='positive', tool_run_ids=[reference])]})
        return {'outlook': {'direction': '상승'}, 'summary_card': {'title': '물량 확보', 'summary': '이행을 확인해요.'},
                'factors': [{'type': factor, 'sticker': '중립', 'sentence': '자료를 확인했어요.'}
                            for factor in ('이슈', '차트', '매크로', '밸류', '수급')],
                'conclusion': {'title': '이행 확인', 'supports': [{'label': '계약', 'tool_run_ids': [reference]}],
                               'burdens': [], 'sentence': '진행 상황을 확인해요.'},
                'issue_detail': {'headline': '공급 계약', 'items': [dict(title_keyword='계약',
                    sentence='판매 물량을 확보했어요.', sentiment='positive', tool_run_ids=[reference])]}}
    _, identity = request(kind, model)
    info = contract_info()
    for source in info['sources']:
        target = tmp_path / 'vault' / source['vault_path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('test contract ' + source['vault_path'], encoding='utf-8')
        source['sha256'] = sha256(target.read_text(encoding='utf-8').encode('utf-8')).hexdigest()
    monkeypatch.setattr(screen_validation, 'contract_info', lambda: info)
    with factory() as connection:
        report = read_contract_audit(connection, kind, identity, vault=tmp_path / 'vault')
        assert report['status'] == 'passed', report['entries']
        assert len(report['entries']) == (3 if kind == 'movement' else 11)
        assert report['entries'][0]['data'] == assemble_screen(connection, kind, identity, 'all')
        # Change a persisted value that still satisfies SQL constraints. The auditor
        # must reject it, rather than validating the earlier model/file artifact.
        if kind == 'outlook':
            connection.execute("UPDATE outlook_items SET bullets=jsonb_set(bullets,'{0,is_updated}','true') WHERE analysis_id=%s AND section='detail'", (identity,))
            connection.execute("UPDATE outlook_factor_metrics SET position=200 WHERE analysis_id=%s AND metric_key='ma20_distance_pct'", (identity,))
        else:
            connection.execute('UPDATE movement_items SET source_as_of=NULL WHERE analysis_id=%s', (identity,))
    with factory() as connection:
        failed = read_contract_audit(connection, kind, identity, vault=tmp_path / 'vault')
        assert failed['status'] == 'failed'
        entry = failed['entries'][0]
        if kind == 'outlook':
            assert any(c['id'] == 'initial-body' and c['status'] == 'failed' for c in entry['checks'])
            assert entry['data']['detail']['items'][0]['sentences'][0]['is_updated'] is True
            detail = next(e for e in failed['entries'] if e['view'] == 'outlook-detail')
            assert detail['status'] == 'failed'
            cards = next(e for e in failed['entries'] if e['view'] == 'factor-details')
            assert cards['status'] == 'failed'
        else:
            assert any(e['path'] == '/items/0/source_as_of' for e in entry['schema_errors'])


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
    def request(kind, model_call, fixture=None, identity=None, system_prompt=None):
        identity=identity or 'service-test-'+uuid4().hex
        if identity not in identities:
            identities.append(identity)
        fixture=fixture or make_fixture()
        return execute_request(kind=kind,fixture=fixture,connection_factory=factory,key='fake-key',
            artifacts=tmp_path/identity,analysis_id=identity,model_call=model_call,system_prompt=system_prompt), identity
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


def test_saved_prompt_snapshot_reaches_actual_model_boundary(run_context):
    request, _ = run_context
    async def model(**kwargs):
        assert kwargs['prompt'] == 'version-pinned system instruction'
        assert kwargs['cache_scope'] == 'synthetic:movement:' + kwargs['initial']['context']['etf_code']
        return {'new_items': [], 'selected_item_ids': [], 'summary': None}
    request('movement', model, system_prompt='version-pinned system instruction')


def test_corrupt_source_data_fails_before_paying_for_model(run_context):
    request, factory = run_context
    fixture = make_fixture()
    fixture['macro'].append(dict(fixture['macro'][-1]))
    calls = []
    async def model(**kwargs):
        calls.append(True)
        raise AssertionError('Model must not run with conflicting source observations')
    with pytest.raises(ValueError, match="duplicate macro"):
        request('outlook',model,fixture)
    assert not calls


def test_movement_calls_commit_before_final_publication_and_retry_does_not_call_model(run_context):
    request, factory=run_context
    calls=[]
    async def model(**kwargs):
        calls.append(True)
        reference=news_reference(kwargs)
        with factory() as connection:
            assert connection.execute('''SELECT r.status FROM tool_runs r JOIN tool_definitions d USING(tool_id)
                WHERE r.movement_analysis_id=%s AND d.function_name='get_issue_evidence' ''',
                (kwargs['artifacts'].name,)).fetchone()==('completed',)
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
        assert connection.execute('SELECT count(*) FROM tool_runs WHERE movement_analysis_id=%s',(identity,)).fetchone()==(5,)
        assert connection.execute('SELECT count(*) FROM movement_items WHERE analysis_id=%s',(identity,)).fetchone()==(0,)


@pytest.mark.parametrize("missing_financials", [False, True])
def test_outlook_body_and_independent_features_publish_with_factor_cards(run_context, missing_financials):
    request,factory=run_context
    async def model(**kwargs):
        reference=news_reference(kwargs)
        kwargs['call']('get_instrument_factors', {'instrument_id': kwargs['initial']['context']['etf_code']})
        kwargs['call']('get_instrument_factors', {'instrument_id': '000660', 'factors': ['valuation']})
        kwargs['call']('write_outlook_body',{'title':'물량 확대를 확인해요','items':[
            dict(id='supply',title_keyword='공급 확대',sentences=['추가 공급 계약을 확보했어요.'],sentiment='positive',tool_run_ids=[reference])]})
        return {'outlook':{'direction':'상승'},'summary_card':{'title':'공급 확대','summary':'계약 이행을 확인해요.'},
            'factors':[{'type':f,'sticker':'중립','sentence':'기간별 관측값을 확인했어요.'} for f in ('이슈','차트','매크로','밸류','수급')],
            'conclusion':{'title':'계약 이행 확인','supports':[{'label':'계약','tool_run_ids':[reference]}],'burdens':[],'sentence':'공급 이행을 지켜봐요.'},
            'issue_detail':{'headline':'공급 일정 확인','items':[dict(title_keyword='공급 계약',sentence='물량을 확보했어요.',sentiment='positive',tool_run_ids=[reference])]}}
    fixture = make_fixture()
    if missing_financials:
        fixture['financials'] = []
    result,identity=request('outlook',model,fixture)
    assert set(result)=={'outlook','summary_card','detail','factors','conclusion'}
    assert result['detail']['items'][0]['sentences'][0]['is_updated'] is False
    with factory() as connection:
        assert connection.execute('SELECT count(*) FROM outlook_factor_metrics WHERE analysis_id=%s',(identity,)).fetchone()[0]>10
        assert connection.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s',(identity,)).fetchone()==('completed',)
        screens = read_factor_details(connection, identity)
        if missing_financials:
            assert not {'weighted_per', 'weighted_pbr'} & {m['key'] for m in screens['밸류']['metrics']}
        with connection.cursor() as cur:
            cur.execute("SELECT output FROM tool_runs r JOIN tool_definitions d USING(tool_id) WHERE outlook_analysis_id=%s AND d.function_name='get_instrument_factors' ORDER BY started_at LIMIT 1", (identity,))
            saved = cur.fetchone()[0]
            assert saved['result']['instrument_id'] == fixture['context']['etf_code']
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
