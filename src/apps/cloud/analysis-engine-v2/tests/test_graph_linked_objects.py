"""Following a relation must keep direction, source identity and missing start objects visible."""
import jsonschema
import pytest

from edge_analysis_v2.tools.graph.provider import GraphTools

COLUMNS = [{'property': p, 'mappingStatus': 'ready'} for p in ('id', 'name')]
CATALOG = {'modelChanged': False, 'objects': [{'id': kind, 'titleProperty': 'name', 'columns': COLUMNS} for kind in ('Company', 'Equity')],
    'relations': [{'id': 'Company_Issues_Equity', 'source': 'Company', 'target': 'Equity', 'physicalMapping': {'kind': 'object_fk'}},
                  {'id': 'Equity_Blocked_Equity', 'source': 'Equity', 'target': 'Equity', 'physicalMapping': {'kind': 'blocked'}}]}


def make(tmp_path):
    calls = []
    def run(statement, parameters):
        calls.append((statement, parameters))
        if '-[r:' in statement:
            return [{'sourceId': 'c1', 'id': 'e1', 'properties': {'name': 'Common'}, 'edge': {'key_0': 'e1'}},
                    {'sourceId': 'c1', 'id': 'e2', 'properties': {'name': 'Preferred'}, 'edge': {'key_0': 'e2'}}]
        return [{'id': i, 'properties': {'name': i}} for i in parameters['ids'] if i != 'missing']
    return GraphTools(run, CATALOG, tmp_path, '2026-10-05T00:00:00+00:00'), calls


def test_every_linked_object_is_returned_with_its_source_and_a_missing_start_object_stays_visible(tmp_path):
    tools, calls = make(tmp_path)
    refs = [{'object_type': 'Company', 'object_id': 'c1'}, {'object_type': 'Company', 'object_id': 'missing'}]
    result = tools.call('get_linked_objects', {'object_refs': refs, 'link_type': 'Company_Issues_Equity'})['result']
    assert [(i['source']['object_id'], i['target']['object_id'], i['direction']) for i in result['items']] == [
        ('c1', 'e1', 'forward'), ('c1', 'e2', 'forward')]
    assert result['selection']['completeness'] == 'partial'
    assert [i['status'] for i in result['selection']['items']] == ['resolved', 'not_found_at_cutoff']
    assert '(a:`Company`)-[r:`Company_Issues_Equity`]->(n:`Equity`)' in calls[-1][0]


def test_reverse_starts_from_the_target_type_and_a_wrong_start_type_is_a_limitation(tmp_path):
    tools, calls = make(tmp_path)
    equity = [{'object_type': 'Equity', 'object_id': 'e1'}]
    tools.call('get_linked_objects', {'object_refs': equity, 'link_type': 'Company_Issues_Equity', 'direction': 'reverse'})
    assert '(a:`Equity`)<-[r:`Company_Issues_Equity`]-(n:`Company`)' in calls[-1][0]
    wrong = tools.call('get_linked_objects', {'object_refs': equity, 'link_type': 'Company_Issues_Equity'})['result']
    assert wrong == {'status': 'invalid_or_unavailable', 'reason': 'Reference type does not match link direction'}


def test_unmapped_links_are_limitations_and_unknown_links_never_reach_the_graph(tmp_path):
    tools, calls = make(tmp_path)
    equity = [{'object_type': 'Equity', 'object_id': 'e1'}]
    blocked = tools.call('get_linked_objects', {'object_refs': equity, 'link_type': 'Equity_Blocked_Equity'})['result']
    assert blocked['reason'] == 'Link mapping is not implemented'
    before = len(calls)
    with pytest.raises(jsonschema.ValidationError):
        tools.call('get_linked_objects', {'object_refs': equity, 'link_type': 'Company_Owns_Equity'})
    assert len(calls) == before
