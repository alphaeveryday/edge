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


def test_whole_etf_figures_need_seventy_percent_of_the_weight_and_always_carry_the_observed_share():
    from edge_analysis_v2.tools.fixture_data.common import holdings
    # WHY: a fund whose stored weights sum to 99.71% (cash, rounding) is still the fund; one seen at 69% is not.
    usable = holdings(source())
    assert (usable['coverage'], usable['observed_weight_ratio'], usable['holdings'][0]['weight']) == ('partial', .9971, .9971)
    data = source()
    data['holdings'][0]['weight'] = .7
    assert holdings(data)['observed_weight_ratio'] == .7
    data['holdings'][0]['weight'] = .6999
    with pytest.raises(ValueError, match='below the 70% coverage'):
        holdings(data)
    assert holdings(data, require_complete=False)['observed_weight_ratio'] == .6999


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
    assert versions['get_instrument_factors'] == 'database-v5'  # Fund ratios follow the 70% per-ratio coverage rule.
    # Whole-ETF figures are allowed from 70% observed weight; the same name must not mix old and new meaning.
    assert versions['get_etf_holdings'] == 'database-v2'
    # The audit store also freezes descriptions: moved citation instructions need a new identity.
    assert versions['get_issue_evidence'] == 'database-v3'  # Final evidence now includes the source URL.
    assert versions['calculate_chart_indicators'] == 'database-v1'          # 모양이 안 바뀐 툴은 그대로
