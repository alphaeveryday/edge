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
