"""A date comparison must keep entries, exits and unknown weights apart from weight changes."""
from edge_analysis_v2.tools.graph.provider import GraphTools

CATALOG = {'modelChanged': False, 'relations': [], 'objects': []}


def make(tmp_path):
    tools = GraphTools(lambda statement, parameters: [], CATALOG, tmp_path, '2026-10-05T00:00:00+00:00')
    def snapshot(etf, day, weights):
        members = [{'object_type': 'Equity', 'object_id': key, 'object': {'title': key.upper()}, 'weight_ratio': w} for key, w in weights]
        selection = {'items': members, 'etf_ref': {'object_type': 'ETF', 'object_id': etf}, 'selected_date': day}
        return tools.result(members, selection=selection)
    tools.register('fake_holdings', 'test', {'etf': {}, 'day': {}, 'weights': {}}, [], snapshot, sources=[])
    def ref(etf, day, weights):
        return tools.call('fake_holdings', {'etf': etf, 'day': day, 'weights': weights})['result']['selection_ref']
    return tools, ref


def test_changes_entries_exits_and_unknown_weights_are_reported_separately(tmp_path):
    tools, ref = make(tmp_path)
    a = ref('etf1', '2026-09-11', [['kept', 0.2422], ['left', 0.05], ['blank', None]])
    b = ref('etf1', '2026-10-02', [['kept', 0.25], ['new', 0.01], ['blank', 0.02]])
    result = tools.call('compare_holdings_dates', {'earlier_ref': a, 'later_ref': b})['result']
    rows = {r['object_id']: r for r in result['items']}
    assert rows['kept']['percentage_point_change'] == '0.7800' and rows['kept']['name'] == 'KEPT'
    assert (rows['left']['earlier_present'], rows['left']['later_present'], rows['left']['percentage_point_change']) == (True, False, None)
    assert (rows['new']['earlier_present'], rows['new']['later_weight']) == (False, 0.01)
    assert rows['blank']['percentage_point_change'] is None   # an unknown weight is not zero
    assert (result['data_scope']['earlier_date'], result['data_scope']['later_date']) == ('2026-09-11', '2026-10-02')
    assert 'do not identify manager trades' in result['data_scope']['interpretation']


def test_different_funds_and_unordered_dates_are_limitations(tmp_path):
    tools, ref = make(tmp_path)
    a, b, other = ref('etf1', '2026-09-11', [['x', 0.1]]), ref('etf1', '2026-10-02', [['x', 0.1]]), ref('etf2', '2026-10-02', [['x', 0.1]])
    assert tools.call('compare_holdings_dates', {'earlier_ref': a, 'later_ref': other})['result']['reason'] == 'Date comparison requires the same ETF'
    assert tools.call('compare_holdings_dates', {'earlier_ref': b, 'later_ref': a})['result']['reason'] == 'Earlier and later actual dates must be ordered'
    assert tools.call('compare_holdings_dates', {'earlier_ref': a, 'later_ref': a})['result']['reason'] == 'Earlier and later actual dates must be ordered'
