"""The agent must only ever see responses whose evidence was stored first."""
import jsonschema
import pytest

from edge_analysis_v2.tools.graph.provider import GraphTools

CATALOG = {'modelChanged': False, 'relations': [], 'objects': [{'id': 'Company', 'titleProperty': 'name', 'columns': [
    {'property': p, 'mappingStatus': 'ready'} for p in ('id', 'name')]}]}


def make(tmp_path, rows=3):
    calls = []
    def run(statement, parameters):
        calls.append(statement)
        return [{'id': str(i), 'properties': {'name': 'Company ' + str(i)}} for i in range(rows)]
    tools = GraphTools(run, CATALOG, tmp_path, '2026-10-05T00:00:00+00:00')
    tools.register('list_companies', {'limit': {'type': 'integer'}}, [],
                   lambda limit=20: tools.result(tools.graph.nodes('Company'), limit=limit), sources=['company'])
    return tools, calls


def test_a_page_is_read_from_stored_evidence_without_another_graph_query(tmp_path):
    tools, calls = make(tmp_path)
    first = tools.call('list_companies', {'limit': 1})['result']
    assert len(first['items']) == 1 and first['total_rows'] == 3 and first['page'] == {'complete': False, 'next_offset': 1}
    page = tools.call('get_result_page', {'dataset_ref': first['dataset_ref'], 'offset': 1, 'limit': 2})['result']
    assert [item['object_id'] for item in page['items']] == ['1', '2'] and page['page']['complete']
    assert len(calls) == 1


def test_an_unanswerable_request_is_stored_as_a_limitation_not_as_an_empty_success(tmp_path):
    tools, _ = make(tmp_path)
    tools.register('fails', {}, [], lambda: (_ for _ in ()).throw(ValueError('no such series')), sources=[])
    output = tools.call('fails', {})
    assert output['result'] == {'status': 'invalid_or_unavailable', 'reason': 'no such series'}
    record = tools.store.calls[-1]
    assert record['error'] == 'no such series' and (tmp_path/(output['tool_run_id']+'.json')).is_file()
    # A failed run cannot be reused as data by a later tool.
    with pytest.raises(ValueError):
        tools.store.reference({'tool_run_id': output['tool_run_id'], 'path': '/dataset'}, 'dataset')


def test_a_broken_graph_connection_is_never_reported_as_missing_data(tmp_path):
    def run(statement, parameters):
        raise ConnectionError('graph unreachable')
    tools = GraphTools(run, CATALOG, tmp_path, '2026-10-05T00:00:00+00:00')
    tools.register('list_companies', {}, [], lambda: tools.result(tools.graph.nodes('Company')), sources=['company'])
    with pytest.raises(ConnectionError):
        tools.call('list_companies', {})
    assert tools.store.calls == []


def test_wrong_arguments_and_unknown_tools_are_rejected_before_any_read(tmp_path):
    tools, calls = make(tmp_path)
    with pytest.raises(jsonschema.ValidationError):
        tools.call('list_companies', {'limit': 'many'})
    with pytest.raises(jsonschema.ValidationError):
        tools.call('list_companies', {'unknown': 1})
    with pytest.raises(ValueError, match='Unknown tool'):
        tools.call('search_everything', {})
    assert calls == [] and tools.store.calls == []


def test_every_registered_tool_has_one_schema_and_one_administrator_definition(tmp_path):
    tools, _ = make(tmp_path)
    names = [s['function']['name'] for s in tools.schemas]
    assert names == [d['function_name'] for d in tools.definitions] and len(names) == len(set(names)) == 10
    assert tools.definitions[-1] == {'tool_id': 'list_companies:graph-v1', 'function_name': 'list_companies',
        'version': 'graph-v1', 'description': 'list_companies', 'source_names': ['company']}
    with pytest.raises(ValueError, match='already registered'):
        tools.register('list_companies', {}, [], lambda: None, sources=[])


def test_a_tool_left_out_of_the_exposed_set_does_not_exist_for_the_agent(tmp_path):
    # WHY: one tool's effect is measured by adding it alone, so the others must be invisible and uncallable.
    tools = GraphTools(lambda s, p: [], CATALOG, tmp_path, '2026-10-05T00:00:00+00:00', expose=['search_objects', 'get_result_page'])
    assert [s['function']['name'] for s in tools.schemas] == [d['function_name'] for d in tools.definitions] == ['get_result_page', 'search_objects']
    with pytest.raises(ValueError, match='Unknown tool'):
        tools.call('list_events', {})
    assert tools.call('search_objects', {'object_type': 'Company'})['result']['total_rows'] == 0
    with pytest.raises(ValueError, match='Unknown tools to expose: search_evnets'):
        GraphTools(lambda s, p: [], CATALOG, tmp_path, '2026-10-05T00:00:00+00:00', expose=['search_evnets'])


def test_a_source_named_by_object_type_is_recorded_as_its_view(tmp_path):
    catalog = {'modelChanged': False, 'relations': [], 'objects': [dict(CATALOG['objects'][0], viewName='ontology_view.company')]}
    tools = GraphTools(lambda s, p: [], catalog, tmp_path, '2026-10-05T00:00:00+00:00')
    tools.register('list_companies', {}, [], lambda: None, sources=['Company', '저장한 결과'])
    assert tools.definitions[-1]['source_names'] == ['ontology_view.company', '저장한 결과']


def test_every_graph_tool_is_described_in_the_standard_order(tmp_path):
    # WHY: each description was one paragraph written at a different time; where an id comes from and what to call
    # next sat in a different place in each, or was missing.
    from edge_analysis_v2.tools.graph import descriptions
    from edge_analysis_v2.tools.graph.catalog import load_catalog
    tools = GraphTools(lambda statement, parameters: [], load_catalog()[0], tmp_path, '2026-10-05T00:00:00+00:00')
    for schema in tools.schemas:
        function = schema['function']
        assert function['name'] in descriptions.TOOLS
        lines = function['description'].split('\n')
        assert lines[1].startswith('Returns: ')
        assert all(line.split(': ')[0] in ('Returns', 'Prerequisite', 'Follow-up', 'Alternative') for line in lines[1:])
        assert all(p.get('description') for p in function['parameters']['properties'].values())
