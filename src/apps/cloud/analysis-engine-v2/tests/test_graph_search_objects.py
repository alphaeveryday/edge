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
    # WHY: a search of 52 rows came back as the 20 asked for and the other 32 were never read.
    assert (len(result['rows']), result['total_rows'], result['page']) == (25, 25, {'complete': True, 'next_offset': None})
    assert result['columns'] == ['name', 'object_id'] and result['same_for_all_rows'] == {'object_type': 'Equity'}
    assert result['data_scope']['complete_within_query'] is True and result['data_scope']['query'] == 'Hyundai'
    assert result['data_scope']['matched_properties'] == ['id', 'name', 'ticker']
    assert result['data_scope']['objects_of_this_type_in_graph'] == 2766
    rest = tools.call('get_result_page', {'dataset_ref': result['dataset_ref'], 'offset': 20})['result']
    assert [r[1] for r in rest['rows']] == ['e20', 'e21', 'e22', 'e23', 'e24'] and rest['columns'] == result['columns'] and len(calls) == 1
    broad, _ = make(tmp_path / 'broad', rows=120)
    none = broad.call('search_objects', {'object_type': 'Equity', 'query': 'Hyundai', 'limit': 100})['result']
    assert (len(none['rows']), none['total_rows']) == (0, 120)


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
    assert schema['function']['parameters']['properties']['object_type']['enum'] == ['Equity']
    with pytest.raises(jsonschema.ValidationError):
        tools.call('search_objects', {'object_type': 'Security', 'query': 'x'})
    assert calls == []


def test_a_result_says_whether_the_cutoff_could_be_applied_to_its_type(tmp_path):
    tools, _ = make(tmp_path, rows=1)
    assert tools.call('search_objects', {'object_type': 'Equity'})['result']['data_scope']['cutoff_applied'] is True


def test_a_name_nothing_matches_comes_back_with_what_each_word_matches(tmp_path):
    # WHY: asked about a fund that is not stored, a run searched 12 times under other spellings and tickers.
    names = {'KODEX': ['KODEX 반도체', 'KODEX 200'], '방산': ['PLUS K방산']}
    def run(statement, parameters):
        if 'count(n)' in statement:
            return [{'total': 40}]
        return [{'id': n, 'properties': {'name': n}} for n in names.get(parameters['text'], [])]
    tools = GraphTools(run, CATALOG, tmp_path, '2026-10-05T00:00:00+00:00')
    scope = tools.call('search_objects', {'object_type': 'Equity', 'query': 'KODEX 방산'})['result']['data_scope']
    assert scope['nothing_has_the_whole_query']['each_word_alone_matches'] == names
    found = tools.call('search_objects', {'object_type': 'Equity', 'query': '방산'})['result']
    assert found['total_rows'] == 1 and 'nothing_has_the_whole_query' not in found['data_scope']


def test_either_reference_of_a_run_reaches_the_part_the_tool_reads(tmp_path):
    # WHY: 9 calls in 45 runs passed the right run with /selection where /dataset was read, and failed.
    tools, _ = make(tmp_path)
    run = tools.call('search_objects', {'object_type': 'Equity', 'query': 'Hyundai'})['result']['dataset_ref']['tool_run_id']
    page = tools.call('get_result_page', {'dataset_ref': {'tool_run_id': run, 'path': '/selection'}, 'offset': 20})['result']
    assert len(page['rows']) == 5


def test_an_object_named_only_by_its_title_keeps_the_title_in_the_table(tmp_path):
    # WHY: an event has no name property; dropping the repeated title would have left its rows unnamed.
    from edge_analysis_v2.tools.graph.shape import table
    rows = [{'object_type': 'SourceEvent', 'object_id': 'e1', 'title': 'CONTRACT / A / 2026-09-03', 'properties': {'displayTitle': 'CONTRACT / A / 2026-09-03', 'eventDate': '2026-09-03'}},
            {'object_type': 'ETF', 'object_id': 'f1', 'title': 'PLUS K방산', 'properties': {'name': 'PLUS K방산'}}]
    columns, values, _ = table(rows, keep_id=True, keep_title=True)
    assert columns == ['object_type', 'name', 'eventDate', 'title', 'object_id']
    assert values == [['SourceEvent', None, '2026-09-03', 'CONTRACT / A / 2026-09-03', 'e1'], ['ETF', 'PLUS K방산', None, None, 'f1']]
