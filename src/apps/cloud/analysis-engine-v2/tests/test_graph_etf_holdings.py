"""Holdings must be the whole stored snapshot of the stated date, with original weights."""
import pytest

from edge_analysis_v2.tools.graph.provider import GraphTools

def columns(*names):
    return [{'property': p, 'mappingStatus': 'ready'} for p in ('id', *names)]

CATALOG = {'modelChanged': False, 'relations': [], 'objects': [
    {'id': 'ETF', 'titleProperty': 'name', 'columns': columns('name')},
    {'id': 'Equity', 'titleProperty': 'name', 'columns': columns('name')},
    {'id': 'ETFHolding', 'titleProperty': 'id', 'columns': columns('etfInstrumentId', 'constituentInstrumentId', 'tradeDate',
                                                                 'weightRatio', 'availableAt')}]}
ETF = {'object_type': 'ETF', 'object_id': 'etf1'}
SNAPSHOTS = {'2026-10-02': [('e%02d' % i, 0.04) for i in range(24)] + [('cash', None)]}


def make(tmp_path, cutoff='2026-10-05T00:00:00+00:00'):
    calls = []
    def run(statement, parameters):
        calls.append((statement, parameters))
        if 'max(h.tradeDate)' in statement:
            days = [d for d in SNAPSHOTS if d <= parameters['day']]
            return [{'day': max(days)}] if days else []
        if ':`ETFHolding`' in statement:
            return [{'id': 'h_' + key, 'properties': {'constituentInstrumentId': key, 'weightRatio': weight}}
                    for key, weight in SNAPSHOTS.get(parameters['day'], [])]
        if ':`Equity`' in statement:
            return [{'id': i, 'properties': {'name': i}} for i in parameters['ids'] if i.startswith('e')]
        return [{'id': i, 'properties': {'name': i}} for i in parameters['ids'] if i == 'etf1']
    return GraphTools(run, CATALOG, tmp_path, cutoff), calls


def call(tools, day, policy='exact', **more):
    return tools.call('get_etf_holdings', {'etf_ref': ETF, 'holdings_date': day, 'date_policy': policy, **more})['result']


def members(tools, result):
    return tools.store.reference(result['selection_ref'], 'selection')['items']


def test_the_selection_holds_every_constituent_even_when_the_visible_page_is_short(tmp_path):
    tools, _ = make(tmp_path)
    result = call(tools, '2026-10-02', limit=5)
    assert len(result['items']) == 5 and result['total_rows'] == 25 and result['page']['next_offset'] == 5
    assert result['items'][0] == {'name': 'e00', 'ticker': None, 'weight_ratio': 0.04, 'status': 'resolved',
                                  'object_type': 'Equity', 'object_id': 'e00'}
    assert result['selection']['members'] == 25 and 'items' not in result['selection']
    stored = members(tools, result)
    # 24 x 4% is 96%: the stored weights are returned as they are, never scaled to 100%.
    assert len(stored) == 25 and sum(m['weight_ratio'] for m in stored if m['weight_ratio'] is not None) == pytest.approx(0.96)


def test_an_asset_that_is_not_a_known_security_stays_in_the_list_as_unresolved(tmp_path):
    tools, _ = make(tmp_path)
    result = call(tools, '2026-10-02', limit=100)
    cash = result['items'][-1]
    assert (cash['object_id'], cash['status'], cash['object_type'], cash['weight_ratio']) == ('cash', 'unresolved', None, None)
    assert result['selection']['completeness'] == 'partial' and members(tools, result)[-1]['status'] == 'unresolved'


def test_a_date_without_a_snapshot_fails_under_exact_and_names_the_way_to_get_the_nearest_one(tmp_path):
    tools, _ = make(tmp_path)
    missing = call(tools, '2026-10-03')
    assert missing['status'] == 'invalid_or_unavailable' and 'latest_on_or_before' in missing['reason']
    nearest = call(tools, '2026-10-03', 'latest_on_or_before')['selection']
    assert (nearest['requested_date'], nearest['selected_date'], nearest['staleness_days']) == ('2026-10-03', '2026-10-02', 1)


def test_no_earlier_snapshot_is_a_limitation_not_an_empty_portfolio(tmp_path):
    tools, _ = make(tmp_path)
    assert call(tools, '2026-09-01', 'latest_on_or_before')['status'] == 'invalid_or_unavailable'


def test_a_market_day_after_the_cutoff_is_rejected_before_any_graph_read(tmp_path):
    tools, calls = make(tmp_path, cutoff='2026-10-04T16:00:00+00:00')  # 01:00 on 5 Oct in Korea
    assert call(tools, '2026-10-06')['reason'] == 'Requested date is after the analysis cutoff'
    assert calls == []
    assert str(tools.check_date('2026-10-05')) == '2026-10-05'


def test_only_an_existing_etf_can_be_asked_for_holdings(tmp_path):
    tools, _ = make(tmp_path)
    wrong = tools.call('get_etf_holdings', {'etf_ref': {'object_type': 'Equity', 'object_id': 'e01'},
                                            'holdings_date': '2026-10-02', 'date_policy': 'exact'})['result']
    assert wrong['reason'] == 'ETF reference required'
    absent = tools.call('get_etf_holdings', {'etf_ref': {'object_type': 'ETF', 'object_id': 'nope'},
                                             'holdings_date': '2026-10-02', 'date_policy': 'exact'})['result']
    assert absent['reason'].startswith('ETF not found: object_id must be the id returned by')
