"""The packaged catalog must be usable by the graph reader without the ontology library."""
import json

import pytest

from edge_analysis_v2.tools.graph.catalog import load_catalog
from edge_analysis_v2.tools.graph.facts import GraphFacts


def test_packaged_catalog_matches_reviewed_model_and_has_no_dangling_links():
    catalog, digest = load_catalog()
    graph = GraphFacts(lambda statement, parameters: [], catalog, '2026-10-05T00:00:00+00:00')
    assert len(digest) == 64
    assert graph.objects and graph.links
    assert all(link['source'] in graph.objects and link['target'] in graph.objects
               for link in graph.links.values())
    assert all(graph.properties(kind) for kind in graph.objects)


def test_catalog_from_changed_definitions_is_rejected(tmp_path):
    path = tmp_path/'catalog.json'
    path.write_text(json.dumps({'modelChanged': True}), encoding='utf8')
    with pytest.raises(ValueError, match='reviewed model'):
        load_catalog(path)
