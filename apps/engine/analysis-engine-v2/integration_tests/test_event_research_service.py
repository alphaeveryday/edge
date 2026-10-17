"""Exercise real publication boundaries and the initial/revision model branches."""
from copy import deepcopy
from datetime import datetime, timedelta
import os
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.analysis.service import execute_request
from edge_analysis_v2.tools.fixture_data import FixtureTools, make_fixture


@pytest.fixture
def local_runs(tmp_path):
    dsn = os.environ.get('V2_NEWS_TEST_DSN')
    if not dsn:
        pytest.skip('V2_NEWS_TEST_DSN is required for real PostgreSQL publication verification')
    config = conninfo_to_dict(dsn)
    assert config.get('host') == '127.0.0.1' and config.get('port') in {'15449', '55439'}
    assert config.get('dbname') == 'analysis_v2'
    ids = []
    def factory():
        return psycopg.connect(dsn, autocommit=True)
    def execute(source, model, **kwargs):
        identity = 'news-service-' + uuid4().hex
        ids.append(identity)
        result = execute_request(kind='outlook', source_tools=source, connection_factory=factory,
            analysis_id=identity, artifacts=tmp_path / identity, key='fake-test-key', model_call=model,
            **kwargs)
        return result, identity
    yield execute, factory, ids
    with factory() as conn:
        for identity in reversed(ids):
            conn.execute('DELETE FROM tool_runs WHERE outlook_analysis_id=%s', (identity,))
            for table in ('outlook_items', 'outlook_factors', 'outlook_conclusion_keywords',
                          'outlook_factor_metrics', 'outlook_issue_items', 'outlook_analyses'):
                conn.execute('DELETE FROM ' + table + ' WHERE analysis_id=%s', (identity,))


class SnapshotSource(FixtureTools):
    """Keep source retrieval out of this test; test the real server/DB handoff."""
    data_source = 'database'

    def __init__(self, fixture):
        super().__init__(fixture)
        self.previous_snapshot = 'not_set'

    def news_snapshot(self):
        context = self.fixture['context']
        return {'version': 1, 'snapshot_id': context['analysis_at'],
                'etf_code': context['etf_code'], 'analysis_at': context['analysis_at'],
                'scope': {}, 'events': [], 'documents': [
                    {key: row[key] for key in ('news_id', 'published_at')} for row in self.fixture['news']]}

    def set_previous_news_snapshot(self, snapshot):
        self.previous_snapshot = deepcopy(snapshot)


def fields(reference):
    return {'outlook': {'direction': '상승'},
            'summary_card': {'title': '공급 확대', 'summary': '계약 이행을 확인해요.'},
            'factors': [{'type': f, 'sticker': '중립', 'sentence': '관측값을 확인했어요.'}
                        for f in ('이슈', '차트', '매크로', '밸류', '수급')],
            'conclusion': {'title': '이행 확인', 'supports': [{'label': '계약', 'tool_run_ids': [reference]}],
                           'burdens': [], 'sentence': '진행을 지켜봐요.'},
            'issue_detail': {'headline': '공급 일정', 'items': [dict(title_keyword='계약',
                sentence='물량을 확보했어요.', sentiment='positive', tool_run_ids=[reference])]}}


def write(kwargs, source):
    reference = kwargs['call']('get_issue_evidence',
        {'news_ids': [source.fixture['news'][0]['news_id']], 'include_body': False})['tool_run_id']
    kwargs['call']('write_outlook_body', {'title': '공급 확대를 확인해요', 'items': [dict(
        id='supply', title_keyword='공급 확대', sentences=['추가 물량을 확보했어요.'],
        sentiment='positive', tool_run_ids=[reference])]})
    return fields(reference)


def test_revision_uses_the_completed_predecessors_snapshot_and_real_revision_prompt(local_runs):
    execute, factory, _ = local_runs
    source = SnapshotSource(make_fixture())
    async def first(**kwargs):
        assert kwargs['initial']['news_research']['mode'] == 'initial'
        assert source.previous_snapshot is None
        return write(kwargs, source)
    previous, previous_id = execute(source, first, previous_analysis_id=None)
    updated = deepcopy(source.fixture)
    updated['context']['analysis_at'] = (datetime.fromisoformat(updated['context']['analysis_at']) + timedelta(hours=1)).isoformat()
    current = SnapshotSource(updated)
    async def revision(**kwargs):
        # This is the non-staged branch actually used when an outlook already exists.
        assert kwargs['initial']['previous_analysis'] == previous
        assert kwargs['initial']['previous_issue_detail']['headline'] == '공급 일정'
        assert 'research_state' in kwargs['initial']
        assert all(material.get('definition', {}).get('function_name') != 'capture_news_snapshot'
                   for material in kwargs['initial']['research_state']['materials'].values())
        assert kwargs['effort'] == 'high'
        start = kwargs['initial']['news_research']
        assert start['mode'] == 'revision' and start['baseline_status'] == 'ready'
        assert start['previous_analysis_id'] == previous_id
        assert start['previous_analysis_at'] == source.fixture['context']['analysis_at']
        assert start['recommended_tool'] == 'get_event_changes'
        assert start['previous_news'] == [{'news_id': source.fixture['news'][0]['news_id'],
            'title': source.fixture['news'][0]['title'], 'published_at': source.fixture['news'][0]['published_at'],
            'in_current_scope': True}]
        assert current.previous_snapshot == source.news_snapshot()
        assert 'get_event_changes' in kwargs['prompt']
        assert '새로운 중요 사건' in kwargs['prompt']
        # A valid inherited issue remains publishable without re-fetching its article.
        assert '이슈 상세에는 이번 실행의 근거가 필요하다' not in kwargs['prompt']
        return {}
    result, identity = execute(current, revision, previous_analysis_id=previous_id, staged=True)
    assert result['detail'] == previous['detail']
    with factory() as conn:
        refs = conn.execute('SELECT tool_run_ids FROM outlook_issue_items WHERE analysis_id=%s', (identity,)).fetchone()[0]
        assert refs == previous['detail']['items'][0]['tool_run_ids']


def test_prior_news_includes_an_article_cited_only_by_the_inherited_issue(local_runs):
    execute, _, _ = local_runs
    source = SnapshotSource(make_fixture())
    async def first(**kwargs):
        result = write(kwargs, source)
        reference = kwargs['call']('get_issue_evidence', {
            'news_ids': [source.fixture['news'][1]['news_id']], 'include_body': False})['tool_run_id']
        result['issue_detail']['items'][0]['tool_run_ids'] = [reference]
        return result
    _, previous_id = execute(source, first, previous_analysis_id=None)
    updated = deepcopy(source.fixture)
    updated['context']['analysis_at'] = (datetime.fromisoformat(updated['context']['analysis_at']) + timedelta(hours=1)).isoformat()
    async def revision(**kwargs):
        selected = kwargs['initial']['news_research']['previous_news']
        assert {row['news_id'] for row in selected} == {row['news_id'] for row in source.fixture['news'][:2]}
        assert all(row['in_current_scope'] for row in selected)
        return {}
    execute(SnapshotSource(updated), revision, previous_analysis_id=previous_id)


def test_repair_exhaustion_cannot_publish_an_investigation_with_invented_evidence(local_runs):
    execute, factory, ids = local_runs
    source = SnapshotSource(make_fixture())
    stages = []
    async def invalid(**kwargs):
        stages.append(kwargs['artifacts'].name)
        return {'report': '공급이 늘었어요. [f999]'}
    with pytest.raises(ValueError, match='Investigation evidence failed after repair'):
        execute(source, invalid, previous_analysis_id=None, staged=True)
    assert stages == ['investigation']
    with factory() as conn:
        assert conn.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s', (ids[-1],)).fetchone() == ('failed',)
        assert conn.execute('SELECT count(*) FROM outlook_items WHERE analysis_id=%s', (ids[-1],)).fetchone() == (0,)


def test_a_legacy_predecessor_requires_baseline_establishment_not_a_no_change_claim(local_runs):
    execute, factory, _ = local_runs
    old = FixtureTools(make_fixture())
    old.data_source = 'database'
    async def first(**kwargs):
        return write(kwargs, old)
    _, previous_id = execute(old, first, previous_analysis_id=None)
    fixture = deepcopy(old.fixture)
    fixture['context']['analysis_at'] = (datetime.fromisoformat(fixture['context']['analysis_at']) + timedelta(hours=1)).isoformat()
    current = SnapshotSource(fixture)
    async def revision(**kwargs):
        start = kwargs['initial']['news_research']
        assert start['mode'] == 'revision' and start['baseline_status'] == 'missing'
        assert start['previous_news'][0]['published_at'] is None
        assert current.previous_snapshot is None
        assert start['previous_analysis_at'] == old.fixture['context']['analysis_at']
        return write(kwargs, current)
    execute(current, revision, previous_analysis_id=previous_id)


def test_revision_rechecks_prior_article_by_id_and_keeps_unchanged_body(local_runs):
    execute, factory, _ = local_runs
    source = SnapshotSource(make_fixture())
    async def first(**kwargs):
        return write(kwargs, source)
    previous, previous_id = execute(source, first, previous_analysis_id=None)
    updated = deepcopy(source.fixture)
    updated['context']['analysis_at'] = (datetime.fromisoformat(updated['context']['analysis_at']) + timedelta(hours=1)).isoformat()
    current = SnapshotSource(updated)
    async def revision(**kwargs):
        selected = kwargs['initial']['news_research']['previous_news']
        assert len(selected) == 1 and selected[0]['in_current_scope'] is True
        # When a fact needs rechecking, its known article ID avoids a repeated search.
        reference = kwargs['call']('get_issue_evidence', {
            'news_ids': [selected[0]['news_id']], 'include_body': False})['tool_run_id']
        return fields(reference)
    result, identity = execute(current, revision, previous_analysis_id=previous_id)
    assert result['detail'] == previous['detail']
    with factory() as conn:
        refs = conn.execute('SELECT tool_run_ids FROM outlook_issue_items WHERE analysis_id=%s', (identity,)).fetchone()[0]
        assert refs != previous['detail']['items'][0]['tool_run_ids']
        calls = conn.execute('''SELECT d.function_name FROM tool_runs r JOIN tool_definitions d USING(tool_id)
            WHERE r.outlook_analysis_id=%s''', (identity,)).fetchall()
        assert ('get_issue_evidence',) in calls
        assert not any(name.startswith('search_news') for (name,) in calls)


def test_fields_repair_exhaustion_does_not_borrow_the_first_topics_evidence(local_runs):
    execute, factory, ids = local_runs
    source = SnapshotSource(make_fixture())
    stages, reference = [], None
    async def model(**kwargs):
        nonlocal reference
        stage = kwargs['artifacts'].name
        stages.append(stage)
        if stage == 'investigation':
            reference = kwargs['call']('get_issue_evidence', {
                'news_ids': [source.fixture['news'][0]['news_id']], 'include_body': False})['tool_run_id']
            return {'report': '계약을 확인했어요. [' + reference + ']'}
        if stage == 'writing':
            kwargs['call']('write_outlook_body', {'title': '공급 확대를 확인해요', 'items': [dict(
                id='supply', title_keyword='공급 확대', sentences=['추가 물량을 확보했어요.'],
                sentiment='positive', tool_run_ids=[reference])]})
            return {k: v for k, v in fields(reference).items() if k in ('outlook', 'summary_card')}
        return {k: v for k, v in fields('f999').items() if k not in ('outlook', 'summary_card')}
    with pytest.raises(ValueError, match='Unverified evidence after repair: f999'):
        execute(source, model, previous_analysis_id=None, staged=True)
    assert stages == ['investigation', 'writing', 'fields']
    with factory() as conn:
        assert conn.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s', (ids[-1],)).fetchone() == ('failed',)
        assert conn.execute('SELECT count(*) FROM outlook_items WHERE analysis_id=%s', (ids[-1],)).fetchone() == (0,)


def test_event_discovery_can_publish_evidence_beyond_the_old_thousand_article_boundary(local_runs):
    from edge_analysis_v2.sources.database import DatabaseTools
    execute, factory, _ = local_runs
    data = make_fixture()
    cutoff = datetime.fromisoformat(data['context']['analysis_at'])
    data.update(news=[], news_events=[], news_links=[], news_scope={'lookback_days': 180})
    for index in range(1002):
        identity = f'article-{index}'
        when = (cutoff - timedelta(hours=1 if index < 1001 else 25)).isoformat()
        data['news'].append({'news_id': identity, 'title': identity, 'body': '확인한 기사 발췌',
                            'body_kind': 'excerpt', 'published_at': when, 'available_at': when,
                            'source_uri': 'https://example.com/' + identity})
        data['news_events'].append({'event_id': identity, 'event_type': 'CONTRACT', 'event_date': when[:10],
            'stage': 'SIGNED', 'status': 'ACTIVE', 'available_at': when,
            'thread_id': 'repeated' if index < 1001 else 'important',
            'novelty': 'DUPLICATE_REBROADCAST' if index < 1001 else 'CORRECTION',
            'evaluated_at': when, 'news_ids': [identity], 'issuer_ids': []})
    source = DatabaseTools(data)
    async def model(**kwargs):
        call = kwargs['call']
        found = call('list_events', {})['result']
        assert found['total_count'] == 2
        timeline = call('get_event_timeline', {'event_key': 'thread:important'})['result']
        ids = timeline['items'][0]['news_ids']
        assert ids == ['article-1001']
        assert call('get_issue_evidence', {'news_ids': ids, 'include_body': True})['result']['news'][0]['body']
        reference = call('get_issue_evidence', {'news_ids': ids, 'include_body': False})['tool_run_id']
        call('write_outlook_body', {'title': '정정 사실을 반영해요', 'items': [dict(
            id='correction', title_keyword='정정 확인', sentences=['정정된 사실을 확인했어요.'],
            sentiment='negative', tool_run_ids=[reference])]})
        return fields(reference)
    published, identity = execute(source, model, previous_analysis_id=None)
    assert published['detail']['items'][0]['source_links'][0]['url'] == 'https://example.com/article-1001'
    with factory() as conn:
        assert conn.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s', (identity,)).fetchone() == ('completed',)
