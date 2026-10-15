"""Independent scenario runs must not inherit another branch's explanations."""

import os
from datetime import datetime
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from edge_analysis_v2.analysis.service import _previous, execute_request
from edge_analysis_v2.tools.fixture_data import make_fixture


@pytest.fixture
def lineage(tmp_path):
    dsn = os.environ['V2_FACTOR_TEST_DSN']
    settings = conninfo_to_dict(dsn)
    if (settings.get('host') != '127.0.0.1' or settings.get('port') != '55440'
            or settings.get('dbname') != 'analysis_v2'):
        raise ValueError('Dedicated local factor test database required')
    etf = 'lineage-' + uuid4().hex
    identities = []
    def factory():
        return psycopg.connect(dsn, autocommit=True)
    with factory() as connection:
        before = {row[0] for row in connection.execute('SELECT tool_id FROM tool_definitions')}
    def request(minute, model, **predecessor):
        identity = etf + '-' + str(len(identities))
        identities.append(identity)
        fixture = make_fixture(analysis_at=f'2026-09-21T10:{minute:02d}:00+09:00')
        fixture['context']['etf_code'] = etf
        result = execute_request(kind='movement', fixture=fixture, connection_factory=factory,
            key='fake-key', artifacts=tmp_path/identity, analysis_id=identity,
            model_call=model, **predecessor)
        return identity, result
    try:
        yield request, factory, etf
    finally:
        with factory() as connection:
            connection.execute('DELETE FROM tool_runs WHERE movement_analysis_id IN (SELECT analysis_id FROM movement_analyses WHERE etf_code=%s)', (etf,))
            connection.execute('DELETE FROM movement_items WHERE analysis_id IN (SELECT analysis_id FROM movement_analyses WHERE etf_code=%s)', (etf,))
            connection.execute('DELETE FROM movement_analyses WHERE etf_code=%s', (etf,))
            created = {row[0] for row in connection.execute('SELECT tool_id FROM tool_definitions')} - before
            for tool_id in created:
                connection.execute('DELETE FROM tool_definitions WHERE tool_id=%s AND NOT EXISTS (SELECT FROM tool_runs WHERE tool_id=%s)', (tool_id, tool_id))


def explain(label):
    async def model(**kwargs):
        assert kwargs['initial']['previous_analysis'] is None
        assert kwargs['initial']['previous_items'] == []
        run = kwargs['call']('get_issue_evidence', {'news_ids': [kwargs['initial']['news'][0]['news_id']], 'include_body': False})
        return {'new_items': [{'candidate_id': label, 'type': '이슈', 'title_keyword': label,
            'sentence': label, 'sentiment': 'positive', 'tool_run_ids': [run['tool_run_id']]}],
            'selected_item_ids': [label], 'summary': label}
    return model


def test_explicit_predecessor_sees_only_its_ancestors_and_keeps_that_branch(lineage):
    request, _, _ = lineage
    first, first_screen = request(0, explain('first branch'), previous_analysis_id=None)
    request(1, explain('unrelated branch'), previous_analysis_id=None)
    async def unchanged(**kwargs):
        assert kwargs['initial']['previous_analysis'] == first_screen
        assert [item['sentence'] for item in kwargs['initial']['previous_items']] == ['first branch']
        return {'new_items': [], 'selected_item_ids': [], 'summary': None}
    child, child_screen = request(2, unchanged, previous_analysis_id=first)
    assert child_screen == first_screen
    _, grandchild = request(3, unchanged, previous_analysis_id=child)
    assert grandchild == first_screen


def test_explicit_none_starts_empty_even_when_another_scenario_has_published(lineage):
    request, _, _ = lineage
    request(0, explain('other scenario'), previous_analysis_id=None)
    async def no_information(**kwargs):
        assert kwargs['initial']['previous_analysis'] is None
        assert kwargs['initial']['previous_items'] == []
        return {'new_items': [], 'selected_item_ids': [], 'summary': None}
    _, result = request(1, no_information, previous_analysis_id=None)
    assert result == {'summary': None, 'items': []}


def test_latest_same_cutoff_prefers_publication_time_over_identifier(lineage):
    _, factory, etf = lineage
    older, newer = 'z-' + etf, 'a-' + etf
    cutoff = datetime.fromisoformat('2026-09-21T11:00:00+09:00')
    with factory() as connection:
        for identity, published in ((older, '10:01'), (newer, '10:02')):
            connection.execute('''INSERT INTO movement_analyses
                (analysis_id,etf_code,analysis_at,trading_date,status,published_at,data_source)
                VALUES (%s,%s,'2026-09-21T10:00:00+09:00','2026-09-21','completed',%s,'synthetic')''',
                (identity, etf, '2026-09-21T'+published+':00+09:00'))
        assert _previous(connection, 'movement', etf, cutoff) == newer
