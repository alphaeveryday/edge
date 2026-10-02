"""Only evidence backing the selected publication may reach the admin table."""

import os
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from edge_analysis_v2.storage import inspection
from edge_analysis_v2.storage.publications import PublicationStore
from edge_analysis_v2.storage.tool_runs import ToolStore


@pytest.fixture
def saved():
    dsn = os.environ['V2_EVIDENCE_TEST_DSN']
    args = conninfo_to_dict(dsn)
    if (args.get('host'), args.get('port'), args.get('dbname')) != (
            '127.0.0.1', '55446', 'analysis_v2'):
        raise ValueError('Dedicated local evidence test database required')
    key = uuid4().hex
    now = datetime.fromisoformat('2026-10-02T10:00:00+09:00')
    with psycopg.connect(dsn, autocommit=True) as conn:
        store = PublicationStore(conn, final_tool_names={'sum', 'get_issue_evidence'})
        audit = ToolStore(conn)
        old, current = key + '-old', key + '-current'
        definitions = [key + '-sum', key + '-news']
        audit.register_definition(tool_id=definitions[0], function_name='sum', version=key,
                                  description='외국인 순매수 금액 합계',
                                  source_names=['일별 투자자 수급'], formula_latex=r'\sum_{d=1}^{5} N_d')
        audit.register_definition(tool_id=definitions[1], function_name='get_issue_evidence',
                                  version=key, description='기사 식별', source_names=['뉴스'])
        try:
            for index, identity in enumerate((old, current)):
                store.begin('movement', identity, key, now + timedelta(minutes=index))
                for suffix in ('sum', 'news', 'unused'):
                    run_id = identity + '-' + suffix
                    is_news = suffix == 'news'
                    audit.save_run(tool_run_id=run_id, tool_id=definitions[int(is_news)],
                                   analysis_kind='movement', analysis_id=identity,
                                   arguments={'include_body': False, 'news_ids': ['news-1']} if is_news
                                   else {'investor': 'foreign', 'days': 5},
                                   context={'flow_as_of_date': '2026-10-01'},
                                   output={'tool_run_id': run_id, 'result':
                                           {'news': [{'news_id': 'news-1', 'title': '계약 체결'}]} if is_news
                                           else {'amount_krw': 1300000000}},
                                   started_at=now, finished_at=now)
                for suffix in ('selected', 'unselected'):
                    conn.execute('''INSERT INTO movement_items
                        (item_id, analysis_id, type, title_keyword, sentence, sentiment, tool_run_ids)
                        VALUES (%s,%s,'수급','제목','설명','positive',%s)''',
                                 (identity + '-' + suffix, identity,
                                  [identity + '-sum', identity + '-news'] if suffix == 'selected'
                                  else [identity + '-unused']))
                conn.execute("UPDATE movement_analyses SET status='completed',published_at=%s WHERE analysis_id=%s",
                             (now, identity))
            selected = [current + '-selected', old + '-selected']
            conn.execute('UPDATE movement_analyses SET selected_item_ids=%s WHERE analysis_id=%s',
                         (selected, current))
            yield conn, old, current, selected
        finally:
            conn.execute('DELETE FROM movement_items WHERE analysis_id=ANY(%s)', ([old, current],))
            conn.execute('DELETE FROM tool_runs WHERE movement_analysis_id=ANY(%s)', ([old, current],))
            conn.execute('DELETE FROM movement_analyses WHERE analysis_id=ANY(%s)', ([old, current],))
            conn.execute('DELETE FROM tool_definitions WHERE tool_id=ANY(%s)', (definitions,))


def test_only_selected_evidence_includes_reused_runs_in_display_order(saved):
    conn, old, current, selected = saved
    result = inspection.read_published_movement_evidence(conn, current)
    assert [item['item_id'] for item in result['items']] == selected
    assert [run['tool_run_id'] for run in result['tool_runs']] == [
        current + '-sum', current + '-news', old + '-sum', old + '-news']
    assert result['analysis']['analysis_id'] == current


def test_shared_evidence_is_listed_once_without_losing_item_links(saved):
    conn, old, current, selected = saved
    conn.execute('UPDATE movement_items SET tool_run_ids=%s WHERE item_id=%s',
                 ([old + '-sum'], selected[0]))
    result = inspection.read_published_movement_evidence(conn, current)
    assert [r['tool_run_id'] for r in result['tool_runs']] == [old + '-sum', old + '-news']
    assert result['items'][0]['tool_run_ids'] == [old + '-sum']
    assert result['items'][1]['tool_run_ids'] == [old + '-sum', old + '-news']


def test_stored_output_and_definition_are_not_recomputed_or_rewritten(saved):
    conn, _, current, _ = saved
    output = {'tool_run_id': current + '-sum', 'result': {'value': '0.000123456789', 'missing': None}}
    conn.execute('UPDATE tool_runs SET output=%s WHERE tool_run_id=%s',
                 (Jsonb(output), current + '-sum'))
    result = inspection.read_published_movement_evidence(conn, current)['tool_runs'][0]
    assert result['output'] == output
    assert result['arguments'] == {'investor': 'foreign', 'days': 5}
    assert result['formula_latex'] == r'\sum_{d=1}^{5} N_d'
    assert result['description'] == '외국인 순매수 금액 합계'
    assert result['source_names'] == ['일별 투자자 수급']
    assert result['context'] == {'flow_as_of_date': '2026-10-01'}


@pytest.mark.parametrize('damage', ['item_missing', 'run_missing', 'references_empty', 'run_failed',
                                   'foreign_etf', 'foreign_source', 'future', 'wrong_day'])
def test_broken_references_are_not_presented_as_complete_evidence(saved, damage):
    conn, old, current, selected = saved
    if damage == 'item_missing':
        conn.execute('UPDATE movement_analyses SET selected_item_ids=%s WHERE analysis_id=%s',
                     (['missing'], current))
    elif damage in ('run_missing', 'references_empty'):
        conn.execute('UPDATE movement_items SET tool_run_ids=%s WHERE item_id=%s',
                     ([] if damage == 'references_empty' else ['missing'], selected[0]))
    elif damage == 'run_failed':
        conn.execute("UPDATE tool_runs SET status='failed' WHERE tool_run_id=%s", (old + '-sum',))
    else:
        updates = {'foreign_etf': "etf_code='other'", 'foreign_source': "data_source='database'",
                   'future': "analysis_at=analysis_at+interval '1 hour'",
                   'wrong_day': "trading_date=trading_date-1"}
        conn.execute('UPDATE movement_analyses SET ' + updates[damage] + ' WHERE analysis_id=%s', (old,))
    with pytest.raises(ValueError):
        inspection.read_published_movement_evidence(conn, current)


@pytest.mark.parametrize('state', ['running', 'failed', 'unpublished'])
def test_unpublished_analysis_is_not_ready_for_admin_delivery(saved, state):
    conn, _, current, _ = saved
    conn.execute("UPDATE movement_analyses SET status=%s,published_at=NULL WHERE analysis_id=%s",
                 ('completed' if state == 'unpublished' else state, current))
    with pytest.raises(ValueError):
        inspection.read_published_movement_evidence(conn, current)


def test_missing_analysis_is_distinct_from_broken_evidence(saved):
    conn, _, _, _ = saved
    assert inspection.read_published_movement_evidence(conn, 'missing') is None


@pytest.mark.parametrize('damage', ['foreign_etf', 'foreign_source', 'future'])
def test_valid_item_cannot_hide_an_invalid_tool_owner(saved, damage):
    conn, old, current, selected = saved
    conn.execute('UPDATE movement_analyses SET selected_item_ids=%s WHERE analysis_id=%s',
                 ([selected[0]], current))
    conn.execute('UPDATE movement_items SET tool_run_ids=%s WHERE item_id=%s',
                 ([old + '-sum'], selected[0]))
    updates = {'foreign_etf': "etf_code='other'", 'foreign_source': "data_source='database'",
               'future': "analysis_at=analysis_at+interval '1 hour'"}
    conn.execute('UPDATE movement_analyses SET ' + updates[damage] + ' WHERE analysis_id=%s', (old,))
    with pytest.raises(ValueError, match='Selected evidence'):
        inspection.read_published_movement_evidence(conn, current)


def test_reader_does_not_join_the_callers_existing_write_transaction(saved):
    conn, _, current, _ = saved
    with conn.transaction():
        conn.execute('SELECT 1')
        with pytest.raises(ValueError, match='idle autocommit'):
            inspection.read_published_movement_evidence(conn, current)


def test_publication_writer_and_reader_agree_when_previous_explanation_is_reused(saved):
    conn, old, current, _ = saved
    conn.execute("""UPDATE movement_analyses SET status='running', published_at=NULL,
                    previous_analysis_id=%s WHERE analysis_id=%s""", (old, current))
    store = PublicationStore(conn, final_tool_names={'sum', 'get_issue_evidence'})
    store.save_movement(current, {'new_items': [], 'selected_item_ids': [old + '-selected'],
                                 'summary': '이전 설명이 여전히 가장 중요합니다.'})
    result = inspection.read_published_movement_evidence(conn, current)
    assert result['items'] == [{'item_id': old + '-selected',
                               'tool_run_ids': [old + '-sum', old + '-news']}]
    assert [run['tool_run_id'] for run in result['tool_runs']] == [old + '-sum', old + '-news']
