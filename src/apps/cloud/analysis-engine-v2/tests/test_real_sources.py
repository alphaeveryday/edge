"""Real adapters must expose source limitations without manufacturing evidence."""
import pytest

from edge_analysis_v2.sources.database import DatabaseTools


def source():
    return {
        'context': {'etf_code': '091160', 'analysis_at': '2026-09-30T12:00:00+09:00',
                    'flow_as_of_date': '2026-09-29'},
        'instruments': [{'instrument_id': '091160', 'name': 'ETF'},
                        {'instrument_id': '000001', 'name': 'Stock'}],
        'holdings': [{'instrument_id': '000001', 'weight': .9971,
                      'as_of_date': '2026-09-30', 'available_at': '2026-09-30T08:00:00+09:00'}],
        'holdings_status': [{'as_of_date': '2026-09-30', 'input_count': 2, 'valid_count': 1}],
        'trading_dates': [], 'news': [], 'news_links': [],
    }


def test_partial_portfolio_is_visible_but_not_renormalized():
    tools = DatabaseTools(source())
    result = tools.call('get_etf_holdings', {})['result']
    assert result['coverage'] == 'partial'
    assert result['observed_weight_ratio'] == .9971
    assert result['holdings'][0]['weight'] == .9971
    assert tools.initial_input()['holdings'] == result


def test_partial_portfolio_cannot_be_used_as_whole_etf_flow():
    from edge_analysis_v2.tools.fixture_data.common import holdings
    with pytest.raises(ValueError, match='complete'):
        holdings(source())


def over_one(input_count):
    # 388420 2026-10-02: 원천은 주식 18행 합 100.15% + 원화현금 -0.15%. 적재는 주식 행만 싣는다(input 19, valid 18).
    data = source()
    data['instruments'].append({'instrument_id': '000002', 'name': 'Other'})
    data['holdings'] = [{'instrument_id': code, 'weight': weight, 'as_of_date': '2026-09-30',
                         'available_at': '2026-09-30T08:00:00+09:00'} for code, weight in (('000001', .5193), ('000002', .4822))]
    data['holdings_status'] = [{'as_of_date': '2026-09-30', 'input_count': input_count, 'valid_count': 2}]
    return data


def test_equities_above_one_with_an_unloaded_source_row_are_input_but_never_a_whole_etf_weight():
    # WHY(ALPHA-1162): 원천의 현금 행이 음수면 주식 비중 합이 100% 를 넘는다. 현금 행은 적재되지 않으므로 이 상태는
    # 현금이 양수인 ETF(합 < 1, coverage=partial)와 같은 "전체 포트폴리오 미확인"이다. 부호가 다르다는 이유로 뉴스·가격·
    # 매크로까지 쓰는 전망 전체를 막지 않되, 비중은 원천 그대로 두고(재정규화 금지) 전체 ETF 가중 계산에는 쓰지 않는다.
    from edge_analysis_v2.tools.fixture_data import instrument_factors, valuation
    from edge_analysis_v2.tools.fixture_data.common import holdings
    data = over_one(input_count=3)
    result = DatabaseTools(data).call('get_etf_holdings', {})['result']
    assert result['coverage'] == 'partial'
    assert result['observed_weight_ratio'] == 1.0015
    assert [row['weight'] for row in result['holdings']] == [.5193, .4822]
    with pytest.raises(ValueError, match='complete'):
        holdings(data)  # 가중 수급·가중 밸류에이션 도구가 쓰는 엄격 판정은 그대로 거부한다
    with pytest.raises(ValueError, match='complete'):
        valuation.weighted(data)
    assert instrument_factors.valuation_data(data | {'financials': [], 'prices': []}, '091160') == (
        None, 'Constituent coverage is incomplete; whole-ETF weighted valuation unavailable.')


def test_equities_above_one_with_every_source_row_loaded_stay_rejected():
    # WHY: 적재되지 않은 원천 행이 없는데 합이 1 을 넘으면 초과분을 설명할 행이 없다 — 중복·단위 오류와 구별할 수 없으므로
    # 입력으로도 받지 않는다. 상태 표가 없는 자료(고정 자료)도 같은 이유로 거부를 유지한다.
    from edge_analysis_v2.tools.fixture_data.common import holdings
    with pytest.raises(ValueError, match='summing to one'):
        holdings(over_one(input_count=2), require_complete=False)
    data = over_one(input_count=3)
    del data['holdings_status']
    with pytest.raises(ValueError, match='summing to one'):
        holdings(data, require_complete=False)


def test_one_article_can_belong_to_two_real_events_without_duplicate_article():
    data = source()
    for identity in ('a', 'b', 'unlinked'):
        data['news'].append({'news_id': identity, 'title': identity, 'body': 'excerpt',
            'body_kind': 'excerpt', 'published_at': '2026-09-30T09:00:00+09:00',
            'available_at': '2026-09-30T09:01:00+09:00'})
    data['news_links'] = [dict(news_id=n, thread_id=t, event_id=e, stage=None)
        for n,t,e in [('a','t1','e1'),('a','t2','e2'),('b','t1','e1')]]
    tools = DatabaseTools(data)
    result = tools.call('search_news_threads', {})['result']
    assert len(result['threads']) == 2
    assert result['threads'][0]['stages'][0]['events'][0]['duplicate_count'] == 1
    assert result['unthreaded_news'][0]['news_id'] == 'unlinked'
    body = tools.call('get_issue_evidence', {'news_ids': ['a'], 'include_body': True})['result']['news'][0]
    assert body['body_kind'] == 'excerpt'
    final = tools.call('get_issue_evidence', {'news_ids': ['a'], 'include_body': False})['result']
    assert final == {'news': [{'news_id': 'a', 'title': 'a'}]}


def test_real_tools_never_create_absent_macro_or_financial_observations():
    tools = DatabaseTools(source())
    initial = tools.initial_input()
    assert tools.data_source == 'database'
    assert not initial['financials']['rows']
    assert all(not value['rows'] for value in initial['macro'].values())


def test_database_factor_tool_version_moves_with_its_response_shape():
    # WHY(봇 P1): get_instrument_factors 응답에 근사 EPS 표시(eps_approximate·weighted_per_approximate)가 더해졌다.
    # 불변 툴 ID(이름:버전) 아래 바뀐 모양과 옛 모양이 섞이면 저장된 근거를 재현할 수 없다 — DB 모드도 버전을 올린다.
    tools = DatabaseTools(source() | {'prices': [], 'price_snapshots': []})
    versions = {d['function_name']: d['version'] for d in tools.definitions}
    assert versions['get_instrument_factors'] == 'database-v3'  # Stored macro/financial sources are now connected.
    assert versions['calculate_chart_indicators'] == 'database-v1'          # 모양이 안 바뀐 툴은 그대로
