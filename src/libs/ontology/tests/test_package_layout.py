from importlib import resources


def test_metadata_and_rules_ship_with_the_library():
    root = resources.files('edge_ontology')
    assert root.joinpath('metadata/value_types/event_types/company_deal.yaml').is_file()
    assert root.joinpath('rules/threading/news_thread_contract_v0_1.yaml').is_file()
    from edge_ontology.oms.events import load_process_registry
    assert len(load_process_registry().types) == 53
