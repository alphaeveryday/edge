"""Explicit PostgreSQL publication atomicity and evidence ownership checks."""

import os
from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from edge_analysis_v2.storage.factors import METRICS, read_factor_details, save_factor_details


@pytest.fixture
def factor_db():
    dsn = os.environ['V2_FACTOR_TEST_DSN']
    config = conninfo_to_dict(dsn)
    if config.get('host') != '127.0.0.1' or config.get('port') != '55440' or config.get('dbname') != 'analysis_v2':
        raise ValueError('Dedicated local factor test database required')
    with psycopg.connect(dsn, autocommit=True) as connection:
        key = 'factor-test-' + uuid4().hex
        with connection.transaction():
            connection.execute("INSERT INTO outlook_analyses (analysis_id,etf_code,analysis_at) VALUES (%s,'TEST','2026-09-21T10:00:00+09:00')", (key,))
            for factor in list(METRICS) + ['이슈']:
                connection.execute("INSERT INTO outlook_factors VALUES (%s,%s,%s,'상승','Test')", (key+factor,key,factor))
            connection.execute("INSERT INTO tool_definitions(tool_id,function_name,version,description) VALUES (%s,'get_issue_evidence',%s,'Test')", (key,key))
            connection.execute('''INSERT INTO tool_runs(tool_run_id,tool_id,outlook_analysis_id,arguments,output,status,finished_at)
                VALUES (%s,%s,%s,'{"include_body":false}','{}','completed',now())''', (key,key,key))
            metric = dict(key='ma20_distance_pct',value=8.2,observed_at='2026-09-18')
            metric_run = key + '-metrics'
            connection.execute("INSERT INTO tool_definitions(tool_id,function_name,version,description) VALUES (%s,'get_instrument_factors',%s,'Test')", (metric_run,key))
            connection.execute('''INSERT INTO tool_runs(tool_run_id,tool_id,outlook_analysis_id,arguments,output,status,finished_at)
                VALUES (%s,%s,%s,%s,%s,'completed',now())''', (metric_run,metric_run,key,
                    Jsonb({'instrument_id':'TEST','factors':['chart']}), Jsonb({'tool_run_id':metric_run,'result':{'instrument_id':'TEST','chart':{'ma20_distance_pct':8.2,'observed_at':'2026-09-18'}}})))
            metrics = {'차트': [dict(metric,tool_run_ids=[metric_run])]}
            issue = dict(headline='계약 물량 확보',items=[dict(title_keyword='계약',sentence='설비 물량 확보',sentiment='positive',tool_run_ids=[key])])
            yield connection, key, metrics, issue
            raise psycopg.Rollback()


def test_roundtrip_preserves_cards_and_parent_sticker_and_writer_can_read(factor_db):
    conn,key,metrics,issue = factor_db
    save_factor_details(conn,key,metrics,issue)
    screen = read_factor_details(conn,key)
    assert screen['차트']['metrics'] == [dict(key='ma20_distance_pct',value=8.2,observed_at='2026-09-18')]
    assert screen['차트']['sticker'] == '상승'
    assert screen['매크로']['metrics'] == []
    assert screen['이슈']['items'] == issue['items']
    conn.execute('SET LOCAL ROLE edge_analysis_v2_writer')
    assert read_factor_details(conn,key) == screen


@pytest.mark.parametrize('fault', ['future','unknown_run','news_body','published'])
def test_rejected_replacement_preserves_previous_details(factor_db,fault):
    conn,key,metrics,issue = factor_db
    save_factor_details(conn,key,metrics,issue)
    expected = read_factor_details(conn,key)
    changed = deepcopy(metrics)
    if fault == 'future':
        changed['차트'][0]['observed_at'] = '2026-09-22'
    elif fault == 'unknown_run':
        changed['차트'][0]['tool_run_ids'] = ['other-analysis-run']
    elif fault == 'news_body':
        conn.execute("UPDATE tool_runs SET arguments='{"+'"include_body":true'+"}' WHERE tool_run_id=%s", (key,))
    else:
        conn.execute("UPDATE outlook_analyses SET status='completed' WHERE analysis_id=%s", (key,))
    with pytest.raises(ValueError):
        save_factor_details(conn,key,changed,issue)
    assert read_factor_details(conn,key) == expected


def test_publication_rollback_includes_factor_details(factor_db):
    conn,key,metrics,issue = factor_db
    with pytest.raises(RuntimeError):
        with conn.transaction():
            save_factor_details(conn,key,metrics,issue)
            raise RuntimeError('Publication failed')
    assert conn.execute('SELECT count(*) FROM outlook_factor_metrics WHERE analysis_id=%s',(key,)).fetchone()[0] == 0
    assert conn.execute('SELECT issue_headline FROM outlook_analyses WHERE analysis_id=%s',(key,)).fetchone()[0] is None


@pytest.mark.parametrize('fault', ['value','observation','subject','key','news_instead_of_calculation','factor'])
def test_metric_must_match_its_final_calculation_result(factor_db,fault):
    conn,key,metrics,issue = factor_db
    metric = metrics['차트'][0]
    if fault == 'value':
        metric['value'] = 99
    elif fault == 'observation':
        metric['observed_at'] = '2026-09-17'
    elif fault == 'subject':
        metric['subject'] = 'different instrument'
    elif fault == 'key':
        metric['key'] = 'atr14_pct'
    elif fault == 'news_instead_of_calculation':
        metric['tool_run_ids'] = [key]
    else:
        conn.execute('UPDATE tool_runs SET arguments=%s WHERE tool_run_id=%s',
                     (Jsonb({'instrument_id':'TEST','factors':['macro']}),key+'-metrics'))
    with pytest.raises(ValueError,match='calculation'):
        save_factor_details(conn,key,metrics,issue)
    assert conn.execute('SELECT count(*) FROM outlook_factor_metrics WHERE analysis_id=%s',(key,)).fetchone()[0] == 0


@pytest.mark.parametrize('values', [
    ('NaN', None, '2026-09-18', None),
    (8, '상승', '2026-09-18', None),
    (8, None, '2026-09-18', '2026-09-18T09:00:00+09:00'),
    (8, None, None, None),
])
def test_database_enforces_single_finite_value_and_observation_precision(factor_db, values):
    conn,key,_,_ = factor_db
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute('''INSERT INTO outlook_factor_metrics
            (analysis_id,factor_type,metric_key,numeric_value,text_value,observed_date,observed_at,position,tool_run_ids)
            VALUES (%s,'차트','ma20_distance_pct',%s,%s,%s,%s,0,%s)''', (key,*values,[key]))
