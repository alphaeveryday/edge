"""Issue details must use the same final-evidence policy as other features."""

import os
from datetime import datetime
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from edge_analysis_v2.publication_store import PublicationStore
from edge_analysis_v2.schemas import FACTORS
from edge_analysis_v2.tool_store import ToolStore


@pytest.mark.parametrize('function,arguments,accepted', [
    ('search_news_threads', {}, False),
    ('get_issue_evidence', {'news_ids': ['n1'], 'include_body': True}, False),
    ('get_issue_evidence', {'news_ids': ['n1'], 'include_body': False}, True),
])
def test_issue_detail_cannot_publish_exploration_evidence(function, arguments, accepted):
    dsn = os.environ['V2_FACTOR_TEST_DSN']
    settings = conninfo_to_dict(dsn)
    if (settings.get('host') != '127.0.0.1' or settings.get('port') != '55440'
            or settings.get('dbname') != 'analysis_v2'):
        raise ValueError('Dedicated local factor test database required')
    identity = 'issue-policy-' + uuid4().hex
    now = datetime.fromisoformat('2026-09-28T08:30:00+09:00')
    with psycopg.connect(dsn, autocommit=True) as connection:
        store = PublicationStore(connection, final_tool_names={'get_issue_evidence'})
        store.begin('outlook', identity, 'TEST', now)
        audit = ToolStore(connection)
        audit.register_definition(tool_id=identity, function_name=function, version=identity,
                                  description='Test article evidence', source_names=['Test news'])
        audit.save_run(tool_run_id=identity, tool_id=identity, analysis_kind='outlook',
                       analysis_id=identity, arguments=arguments, context={},
                       output={'tool_run_id': identity, 'result': {'news': [{'news_id': 'n1', 'title': 'Test news'}]}},
                       started_at=now, finished_at=now)
        features = {'outlook': {'direction': '상승'}, 'summary_card': {'title': '제목', 'summary': '요약'},
                    'factors': [{'type': factor, 'sticker': '상승', 'sentence': '설명'} for factor in FACTORS],
                    'conclusion': {'title': '결론', 'supports': [], 'burdens': [], 'sentence': '설명'}}
        body = {'title': '본문', 'items': [], 'updates': {'date': '2026-09-28', 'items': []}, 'mode': 'create'}
        details = {'metrics': {}, 'issue': {'headline': '계약 체결', 'items': [
            {'title_keyword': '공급 계약', 'sentence': '공급 물량을 확보했어요.',
             'sentiment': 'positive', 'tool_run_ids': [identity]}]}}
        try:
            if accepted:
                result = store.save_outlook(identity, features, body, factor_details=details)
                assert result['outlook']['direction'] == '상승'
                assert connection.execute('SELECT count(*) FROM outlook_issue_items WHERE analysis_id=%s', (identity,)).fetchone()[0] == 1
            else:
                with pytest.raises(ValueError):
                    store.save_outlook(identity, features, body, factor_details=details)
                assert connection.execute('SELECT count(*) FROM outlook_factors WHERE analysis_id=%s', (identity,)).fetchone()[0] == 0
                assert connection.execute('SELECT status FROM outlook_analyses WHERE analysis_id=%s', (identity,)).fetchone()[0] == 'running'
            assert audit.get_run(identity) is not None
        finally:
            for table in ('outlook_issue_items', 'outlook_factor_metrics', 'outlook_factors', 'outlook_items', 'outlook_conclusion_keywords'):
                connection.execute('DELETE FROM ' + table + ' WHERE analysis_id=%s', (identity,))
            connection.execute('DELETE FROM tool_runs WHERE tool_run_id=%s', (identity,))
            connection.execute('DELETE FROM tool_definitions WHERE tool_id=%s', (identity,))
            connection.execute('DELETE FROM outlook_analyses WHERE analysis_id=%s', (identity,))
