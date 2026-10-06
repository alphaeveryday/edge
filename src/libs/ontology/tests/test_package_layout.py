from importlib import resources


def test_metadata_and_rules_ship_with_the_library():
    root = resources.files('edge_ontology')
    assert root.joinpath('metadata/value_types/event_types/company_deal.yaml').is_file()
    assert root.joinpath('rules/threading/news_thread_contract_v0_1.yaml').is_file()
    from edge_ontology.oms.events import load_process_registry
    assert len(load_process_registry().types) == 53


def test_every_view_file_named_by_an_object_definition_ships_with_the_library():
    # WHY: object properties marked ready point at the view that produces them; a missing file means the
    # mapping cannot be recreated from this package.
    import re
    root = resources.files('edge_ontology')
    named = set()
    for definition in root.joinpath('metadata/object_types').iterdir():
        if definition.name.endswith('.yaml'):
            named.update(re.findall(r'sqlFile: (\S+)', definition.read_text(encoding='utf8')))
    assert len(named) > 20
    assert [path for path in sorted(named) if not root.joinpath(path).is_file()] == []
