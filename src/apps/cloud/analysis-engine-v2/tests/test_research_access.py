"""Research must see available observations and know the limits of an empty search."""
from datetime import datetime
from decimal import Decimal

import pytest

from edge_analysis_v2.sources import database
from edge_analysis_v2.tools.fixture_data.common import holdings
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


def _load_stored_weights(monkeypatch, stored, input_rows):
    class Connection:
        read_only = True
        isolation_level = database.psycopg.IsolationLevel.REPEATABLE_READ
    at = datetime.fromisoformat(source()['context']['analysis_at'])
    responses = [
        [{'instrument_id':'etf', 'display_name':'ETF'}],
        [{'trade_date':at.date(), 'input_row_count':input_rows, 'valid_row_count':len(stored), 'data_version':1}],
        [dict(constituent_instrument_id=f'id{i}', ticker=f'{i:06}', market_code='XKRX', display_name=str(i),
              issuer_actor_id=None, weight_ratio=weight, available_at=at) for i, weight in enumerate(stored)], [], [],
    ]
    monkeypatch.setattr(database, '_rows', lambda connection, sql, args: responses.pop(0))
    return database.load_source(Connection(), '069500', at.isoformat())


def test_weight_sum_is_judged_at_the_precision_a_stored_double_carries(monkeypatch):
    # WHY(ALPHA-1162): weight_ratio 는 double 이고, 2026-10-04 이전 적재는 퍼센트를 이진 부동소수로 나눠 저장했다
    # (27.94/100.0 = 0.27940000000000004). 그 값을 그대로 십진수로 더하면 주식 비중 합이 정확히 100.00% 인 스냅샷
    # (069500 2026-10-02)이 1.0000000000000000362 가 되어 분석이 시작 전에 거부됐다. 이미 적재된 값을 다시 쓰지 않고
    # 풀려면 합을 double 이 실제로 담는 15자리에서 판정해야 한다. 비중 값 자체는 바꾸지 않는다.
    pcts = [34.25, 27.94, 2.84, 2.33, 1.36] + [0.31] * 100 + [0.28]  # 069500 상위 다섯 비중(%)과 나머지, 합 100.00
    stored = [pct / 100.0 for pct in pcts]
    assert sum(Decimal(repr(weight)) for weight in stored) > 1  # 고치기 전 거부 조건이 이 자료에서 성립한다
    current = holdings(_load_stored_weights(monkeypatch, stored, len(stored) + 1), require_complete=False)
    assert current['observed_weight_ratio'] == 1
    assert current['coverage'] == 'partial'  # 원천에 현금 행이 하나 더 있다 — 합이 1 이어도 전체 확보로 올리지 않는다
    assert current['holdings'][1]['weight'] == 27.94 / 100.0  # 저장된 비중은 그대로 전달한다(다시 맞추지 않는다)
    assert holdings(_load_stored_weights(monkeypatch, stored, len(stored)), require_complete=False)['coverage'] == 'full'
    # 끝나지 않는 소수(1/6 여섯 개)의 합도 같은 이유로 1 이다 — 값마다 자릿수를 자르면 1.000000000000002 로 거부된다.
    assert holdings(_load_stored_weights(monkeypatch, [1 / 6] * 6, 6), require_complete=False)['coverage'] == 'full'
    # 주식 합이 실제로 100% 를 넘는 스냅샷(388420 2026-10-02 주식 100.15%·현금 -0.15%, 0093A0 100.01%)은 적재되지 않은
    # 원천 행(현금)이 초과분을 설명한다. 그 현금 부호가 음수든, 양수인데 반올림만으로 넘든(261070) 엔진은 현금을 못 보므로
    # 기준은 "적재 안 된 원천 행이 있느냐" 하나다. 있으면 넘는 그대로(재정규화 없이) 부분 확보로 받고, 없으면 초과분을
    # 설명할 행이 없어 중복·단위 오류와 구별되지 않으므로 거부한다.
    for over_pcts, ratio in (((19.22, 9.15, 7.42, 6.39, 6.04, 51.93), 1.0015), ((12.67, 87.34), 1.0001)):
        over = [pct / 100.0 for pct in over_pcts]
        loaded = holdings(_load_stored_weights(monkeypatch, over, len(over) + 1), require_complete=False)
        assert (loaded['observed_weight_ratio'], loaded['coverage']) == (ratio, 'partial')
        with pytest.raises(ValueError, match='summing to one'):
            _load_stored_weights(monkeypatch, over, len(over))
