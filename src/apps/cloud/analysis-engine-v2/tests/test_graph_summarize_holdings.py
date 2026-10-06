"""Weight totals quoted in an answer must come from exact arithmetic over the stored snapshot."""
import pytest

from edge_analysis_v2.tools.graph.provider import GraphTools

CATALOG = {'modelChanged': False, 'relations': [], 'objects': []}
ETF = {'object_type': 'ETF', 'object_id': 'etf1'}


def make(tmp_path, weights, selected_date='2026-10-02', etf_ref=ETF):
    tools = GraphTools(lambda statement, parameters: [], CATALOG, tmp_path, '2026-10-05T00:00:00+00:00')
    members = [{'object_type': 'Equity', 'object_id': key, 'object': {'title': key.upper()}, 'weight_ratio': weight} for key, weight in weights]
    selection = {'items': members, 'etf_ref': etf_ref, 'selected_date': selected_date}
    tools.register('fake_holdings', 'test', {}, [], lambda: tools.result(members, selection=selection), sources=[])
    return tools, tools.call('fake_holdings', {})['result']['selection_ref']


def summary(tools, ref, **more):
    return tools.call('summarize_etf_holdings', {'selection_ref': ref, **more})['result']


def test_the_total_is_exact_decimal_arithmetic_and_is_not_scaled_to_one_hundred(tmp_path):
    tools, ref = make(tmp_path, [('a', 0.1), ('b', 0.2), ('c', 0.0021)])
    row = summary(tools, ref)['items'][0]
    assert row['raw_weight_sum'] == '0.3021' and row['weight_percent'] == '30.2100'   # float addition gives 0.30210000000000004
    assert (row['member_count'], row['status'], row['unknown_weights']) == (3, 'calculated', 0)


def test_top_n_and_an_explicit_subset_name_the_members_they_added(tmp_path):
    tools, ref = make(tmp_path, [('a', 0.1), ('b', 0.5), ('c', 0.3)])
    top = summary(tools, ref, top_n=2)['items'][0]
    assert top['members'] == ['B', 'C'] and top['raw_weight_sum'] == '0.8'
    subset = summary(tools, ref, members=[{'object_type': 'Equity', 'object_id': 'a'}] * 2)['items'][0]
    assert subset['members'] == ['A'] and subset['raw_weight_sum'] == '0.1'


def test_an_unknown_weight_is_counted_as_unknown_never_as_zero(tmp_path):
    tools, ref = make(tmp_path, [('a', 0.4), ('cash', None)])
    row = summary(tools, ref)['items'][0]
    assert (row['raw_weight_sum'], row['unknown_weights'], row['status']) == ('0.4', 1, 'partial')
    assert summary(tools, ref, top_n=1)['reason'] == 'Unknown weights prevent ranking the largest holdings'


@pytest.mark.parametrize('arguments, reason', [
    ({'top_n': 1, 'members': [{'object_type': 'Equity', 'object_id': 'a'}]}, 'Choose an explicit subset or top N, not both'),
    ({'members': [{'object_type': 'Equity', 'object_id': 'zzz'}]}, 'A requested member is not in this saved holding snapshot')])
def test_requests_the_snapshot_cannot_answer_are_limitations(tmp_path, arguments, reason):
    tools, ref = make(tmp_path, [('a', 0.4)])
    assert summary(tools, ref, **arguments)['reason'] == reason


def test_only_a_dated_holdings_selection_is_accepted(tmp_path):
    tools, ref = make(tmp_path, [('a', 0.4)], selected_date=None)
    assert summary(tools, ref)['reason'] == 'A dated ETF holdings selection from get_etf_holdings is required'


def test_the_dataset_reference_of_the_same_holdings_run_is_accepted_in_place_of_the_selection_reference(tmp_path):
    tools, ref = make(tmp_path, [('a', 0.4)])
    row = summary(tools, {'tool_run_id': ref['tool_run_id'], 'path': '/dataset'})['items'][0]
    assert row['raw_weight_sum'] == '0.4'
