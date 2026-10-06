"""A search must return the whole match set as evidence and refuse conditions it cannot verify."""
from edge_analysis_v2.tools.graph.provider import GraphTools

CATALOG = {'modelChanged': False, 'relations': [], 'objects': [{'id': 'Equity', 'titleProperty': 'name', 'columns': [
    {'property': 'id', 'mappingStatus': 'ready'}, {'property': 'name', 'mappingStatus': 'ready'},
    {'property': 'ticker', 'mappingStatus': 'ready'}, {'property': 'availableAt', 'mappingStatus': 'ready'},
    {'property': 'sector', 'mappingStatus': 'unmapped'}]}]}


def make(tmp_path, rows=25):
    calls = []
    def run(statement, parameters):
        if 'count(n)' in statement:
            return [{'total': 2766}]
        calls.append((statement, parameters))
        return [{'id': 'e%02d' % i, 'properties': {'name': 'Hyundai %d' % i}} for i in range(rows)]
    return GraphTools(run, CATALOG, tmp_path, '2026-10-05T00:00:00+00:00'), calls


def test_the_first_page_is_marked_incomplete_and_the_stored_dataset_holds_every_match(tmp_path):
    tools, calls = make(tmp_path)
    output = tools.call('search_objects', {'object_type': 'Equity', 'query': 'Hyundai'})
    result = output['result']
    assert (len(result['items']), result['total_rows'], result['page']) == (20, 25, {'complete': False, 'next_offset': 20})
    assert result['data_scope']['complete_within_query'] is True and result['data_scope']['query'] == 'Hyundai'
    assert result['data_scope']['matched_properties'] == ['id', 'name', 'ticker']
    assert result['data_scope']['objects_of_this_type_in_graph'] == 2766
    rest = tools.call('get_result_page', {'dataset_ref': result['dataset_ref'], 'offset': 20})['result']
    assert [i['object_id'] for i in rest['items']] == ['e20', 'e21', 'e22', 'e23', 'e24'] and len(calls) == 1


def test_the_query_text_is_a_parameter_and_later_records_are_excluded_by_the_cutoff(tmp_path):
    tools, calls = make(tmp_path, rows=0)
    injected = "x' OR 1=1 //"
    tools.call('search_objects', {'object_type': 'Equity', 'query': injected})
    statement, parameters = calls[0]
    assert injected not in statement and parameters['text'] == injected
    assert 'n.availableAt <= datetime($cutoff)' in statement and parameters['cutoff'] == '2026-10-05T00:00:00+00:00'


def test_unknown_types_properties_and_unverified_mappings_are_limitations_not_empty_results(tmp_path):
    tools, calls = make(tmp_path)
    for arguments, reason in [({'object_type': 'Equity', 'filters': {'colour': 'red'}}, 'Unknown property: colour'),
                              ({'object_type': 'Equity', 'filters': {'sector': 'x'}}, 'Property has no verified mapping: sector')]:
        assert tools.call('search_objects', arguments)['result'] == {'status': 'invalid_or_unavailable', 'reason': reason}
    assert calls == []


def test_the_agent_is_offered_the_real_type_names_so_a_guessed_type_never_reaches_the_graph(tmp_path):
    import jsonschema
    import pytest
    tools, calls = make(tmp_path)
    schema = next(s for s in tools.schemas if s['function']['name'] == 'search_objects')
    assert schema['function']['parameters']['properties']['object_type'] == {'enum': ['Equity']}
    with pytest.raises(jsonschema.ValidationError):
        tools.call('search_objects', {'object_type': 'Security', 'query': 'x'})
    assert calls == []


def test_a_result_says_whether_the_cutoff_could_be_applied_to_its_type(tmp_path):
    tools, _ = make(tmp_path, rows=1)
    assert tools.call('search_objects', {'object_type': 'Equity'})['result']['data_scope']['cutoff_applied'] is True
