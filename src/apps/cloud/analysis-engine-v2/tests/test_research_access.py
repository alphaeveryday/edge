"""Research must see available observations and know the limits of an empty search."""
from datetime import datetime

import pytest

from edge_analysis_v2.sources import database
from test_real_sources import source


def test_news_scope_includes_sixth_constituent_and_uses_publication_and_acquisition_cutoff(monkeypatch):
    class Connection:
        read_only = True
        isolation_level = database.psycopg.IsolationLevel.REPEATABLE_READ
    at = datetime.fromisoformat(source()['context']['analysis_at'])
    responses = [
        [{'instrument_id':'etf', 'display_name':'ETF'}],
        [{'trade_date':at.date(), 'input_row_count':6, 'valid_row_count':6, 'data_version':1}],
        [dict(constituent_instrument_id=f'id{i}', ticker=f'{i:06}', market_code='XKRX',
              display_name=str(i), issuer_actor_id=f'actor{i}', weight_ratio=1/6, available_at=at)
         for i in range(6)], [], [],
    ]
    queries = []
    def rows(connection, sql, args):
        queries.append((sql, args))
        return responses.pop(0)
    monkeypatch.setattr(database, '_rows', rows)
    result = database.load_source(Connection(), '091160', at.isoformat())
    sql, args = queries[3]
    assert {'id5', 'actor5'} <= set(args[3])
    assert args[1] == args[2] == at
    assert (at-args[0]).days == 180
    assert 'd.available_at<=%s' in sql and 'd.published_at BETWEEN %s AND %s' in sql
    assert result['news_scope']['universe'].endswith('linked DB news excerpts only')


def test_stored_observations_are_loaded_at_the_analysis_cutoff_and_gaps_survive(monkeypatch):
    calls = []
    def macro(connection, at):
        calls.append(at)
        return [{'series': 'usd_krw', 'value': '1400'}], [{'series': 'kr_cpi_yoy', 'reason': 'no_observation_visible'}]
    def financial(connection, at, ids):
        calls.append(at)
        assert ids == ['000001']
        return [], [{'instrument_id': '000001', 'reasons': {'all': 'no_release_visible'}}]
    monkeypatch.setattr(database, 'macro_inputs', macro, raising=False)
    monkeypatch.setattr(database, 'financial_inputs', financial, raising=False)
    result = database.load_research_observations(object(), source())
    assert calls == [datetime.fromisoformat(source()['context']['analysis_at'])] * 2
    assert result['macro'][0]['value'] == '1400'
    assert result['source_gaps']['financials'][0]['reasons']['all'] == 'no_release_visible'


def test_database_macro_comparison_is_available_without_inventing_missing_series():
    data = source() | {'macro': [dict(series='usd_krw', value=value, unit='KRW_per_USD',
        observed_at=day, available_at='2026-09-30T08:00:00+09:00')
        for day,value in [('2026-09-28', '1400'), ('2026-09-29', '1410')]],
        'financials': [], 'source_gaps': {'financials': [{'reason': 'no_release_visible'}]}}
    tools = database.DatabaseTools(data)
    result = tools.call('compare_macro_observations', dict(series='usd_krw',
        previous_at='2026-09-28', current_at='2026-09-29', operation='difference'))['result']
    assert result['change'] == 10
    assert tools.call('get_macro_observations', {'series': 'kr_cpi_yoy'})['result']['rows'] == []
    assert tools.initial_input()['source_gaps'] == data['source_gaps']


def test_article_search_reaches_older_candidates_and_never_returns_future_evidence():
    data = source()
    data['news_scope'] = {'lookback_days': 180, 'limit_reached': True}
    data['news'] = [dict(news_id=str(i), title='납기 비교', body='고객 구성',
        body_kind='excerpt', published_at='2026-09-29T09:00:00+09:00',
        available_at='2026-09-29T09:01:00+09:00') for i in range(65)]
    data['news'].append(data['news'][0] | {'news_id':'future', 'available_at':'2026-10-01T00:00:00+09:00'})
    tools = database.DatabaseTools(data)
    first = tools.call('search_news_articles', {'query':'납기 고객', 'offset':0})['result']
    second = tools.call('search_news_articles', {'query':'납기 고객', 'offset':first['next_offset']})['result']
    assert len(first['articles']) == 50 and len(second['articles']) == 15
    assert second['next_offset'] is None
    assert 'future' not in {r['news_id'] for r in first['articles'] + second['articles']}
    assert second['scope']['limit_reached']  # Exhausting this snapshot is not exhausting public sources.
    assert 'search_news_articles' not in tools.final_tool_names
    with pytest.raises(ValueError):
        tools.call('search_news_articles', {'query':'납기', 'offset':-1})
