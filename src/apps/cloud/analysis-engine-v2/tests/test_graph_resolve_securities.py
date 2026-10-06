"""Ticker resolution must keep every requested code visible and never guess an identity."""
from edge_analysis_v2.tools.graph.provider import GraphTools

COLUMNS = [{'property': p, 'mappingStatus': 'ready'} for p in ('id', 'name', 'ticker', 'marketCode')]
CATALOG = {'modelChanged': False, 'relations': [], 'objects': [
    {'id': kind, 'titleProperty': 'name', 'columns': COLUMNS} for kind in ('Equity', 'ETF')]}
ROWS = {'Equity': [('eq_a', 'Alpha', '001234')], 'ETF': [('etf_b', 'Beta ETF', '449450'), ('etf_dup', 'Alpha twin', '001234')]}


def make(tmp_path, kinds=('Equity',)):
    calls = []
    def run(statement, parameters):
        calls.append((statement, parameters))
        kind = next(k for k in ROWS if ':`' + k + '`' in statement)
        wanted = parameters['filter_0'] if kind in kinds else []
        return [{'id': i, 'properties': {'name': n, 'ticker': t}} for i, n, t in ROWS[kind] if t in wanted]
    return GraphTools(run, CATALOG, tmp_path, '2026-10-05T00:00:00+00:00'), calls


def test_missing_and_duplicate_codes_stay_in_the_selection_and_one_query_serves_the_batch(tmp_path):
    tools, calls = make(tmp_path)
    result = tools.call('resolve_securities', {'tickers': ['001234', '999999', '001234'], 'market_code': 'XKRX',
                                               'security_type': 'Equity'})['result']
    assert len(calls) == 1 and calls[0][1]['filter_0'] == ['001234', '999999'] and calls[0][1]['filter_1'] == 'XKRX'
    selection = result['selection']
    assert (selection['requested_count'], selection['distinct_count'], selection['completeness']) == (3, 2, 'partial')
    assert [(i['requested_ticker'], i['status'], i['object_id']) for i in selection['items']] == [
        ('001234', 'resolved', 'eq_a'), ('999999', 'not_found_at_cutoff', None)]
    assert result['data_scope']['identity'] == 'exchange market plus exact ticker; no name guessing'


def test_a_code_shared_by_a_stock_and_a_fund_is_ambiguous_with_both_candidates_not_the_first_match(tmp_path):
    tools, calls = make(tmp_path, kinds=('Equity', 'ETF'))
    item = tools.call('resolve_securities', {'tickers': ['001234'], 'market_code': 'XKRX'})['result']['selection']['items'][0]
    assert len(calls) == 2
    assert item['status'] == 'ambiguous' and item['object_id'] is None and item['object'] is None
    assert sorted(c['object_id'] for c in item['candidates']) == ['eq_a', 'etf_dup']


def test_all_codes_resolved_is_complete_and_the_selection_is_reusable_by_reference(tmp_path):
    tools, _ = make(tmp_path, kinds=('Equity', 'ETF'))
    output = tools.call('resolve_securities', {'tickers': ['449450'], 'market_code': 'XKRX'})
    assert output['result']['selection']['completeness'] == 'complete'
    stored = tools.store.reference({'tool_run_id': output['tool_run_id'], 'path': '/selection'}, 'selection')
    assert stored['items'][0]['object_type'] == 'ETF' and stored['items'][0]['object_id'] == 'etf_b'
