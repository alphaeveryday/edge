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
    tools.register('list_companies', 'test', {'limit': {'type': 'integer'}}, [],
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
    tools.register('fails', 'test', {}, [], lambda: (_ for _ in ()).throw(ValueError('no such series')), sources=[])
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
    tools.register('list_companies', 'test', {}, [], lambda: tools.result(tools.graph.nodes('Company')), sources=['company'])
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
    assert names == [d['function_name'] for d in tools.definitions] == ['get_result_page', 'list_companies']
    assert tools.definitions[1] == {'tool_id': 'list_companies:graph-v1', 'function_name': 'list_companies',
        'version': 'graph-v1', 'description': 'test', 'source_names': ['company']}
    with pytest.raises(ValueError, match='already registered'):
        tools.register('list_companies', 'again', {}, [], lambda: None, sources=[])
