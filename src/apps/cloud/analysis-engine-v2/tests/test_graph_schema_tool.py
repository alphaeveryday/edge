"""The schema tool must stay cheap by default and never present definitions as observations."""
from edge_analysis_v2.tools.graph.provider import GraphTools

CATALOG = {'modelChanged': False, 'objects': [
    {'id': kind, 'description': kind + ' description', 'titleProperty': 'name', 'columns': [
        {'property': 'id', 'type': 'string', 'description': 'identifier', 'mappingStatus': 'ready', 'source': 'raw.table'},
        {'column': 'internal_only'}]} for kind in ('ETF', 'ETFHolding', 'Company')],
    'relations': [{'id': 'ETFHolding_ForETF_ETF', 'source': 'ETFHolding', 'target': 'ETF', 'description': 'holding of',
                   'inverse': {'apiName': 'holdings', 'displayName': 'Holdings', 'pluralDisplayName': None}, 'physicalMapping': {'kind': 'object_fk'}}]}


def tools(tmp_path):
    def run(statement, parameters):
        raise AssertionError('schema reads must not query the graph')
    return GraphTools(run, CATALOG, tmp_path, '2026-10-05T00:00:00+00:00')


def test_the_overview_lists_every_type_and_link_but_no_properties(tmp_path):
    result = tools(tmp_path).call('get_ontology_schema', {})['result']
    assert [o['object_type'] for o in result['objects']] == ['ETF', 'ETFHolding', 'Company']
    assert all('properties' not in o for o in result['objects'])
    assert result['links'] == [{'id': 'ETFHolding_ForETF_ETF', 'source': 'ETFHolding', 'target': 'ETF',
                                'description': 'holding of', 'inverse': 'holdings'}]
    assert 'dataset_ref' not in result and result['fact_source'] == 'PuppyGraph'


def test_named_types_return_only_agent_visible_property_fields_and_their_links(tmp_path):
    assert tools(tmp_path).call('get_ontology_schema', {'object_types': ['ETF']})['result']['links'] == ['ETFHolding_ForETF_ETF']
    result = tools(tmp_path).call('get_ontology_schema', {'object_types': ['Company']})['result']
    assert result['objects'] == [{'object_type': 'Company', 'description': 'Company description', 'properties': [
        {'property': 'id', 'type': 'string', 'description': 'identifier', 'mappingStatus': 'ready'}]}]
    assert result['links'] == []


def test_an_unknown_type_is_rejected_by_the_argument_schema(tmp_path):
    import jsonschema
    import pytest
    with pytest.raises(jsonschema.ValidationError):
        tools(tmp_path).call('get_ontology_schema', {'object_types': ['Bond']})
