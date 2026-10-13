from edge_analysis_v2.analysis.service import swap_references


def test_short_references_become_execution_ids_wherever_evidence_is_listed():
    table = {'f1': 'a' * 32, 'f2': 'b' * 32}
    written = {'items': [{'id': 't1', 'sentences': ['f1은 문장 안에서는 그대로'], 'tool_run_ids': ['f2', 'f1']}],
               'conclusion': {'supports': [{'label': 'x', 'tool_run_ids': ['f1', 'c' * 32, 'f9']}]}}
    assert swap_references(written, table) == {
        'items': [{'id': 't1', 'sentences': ['f1은 문장 안에서는 그대로'], 'tool_run_ids': ['b' * 32, 'a' * 32]}],
        # An inherited execution ID and an invented reference both reach the store unchanged.
        'conclusion': {'supports': [{'label': 'x', 'tool_run_ids': ['a' * 32, 'c' * 32, 'f9']}]}}


def test_every_listed_reference_of_a_response_is_found_wherever_it_is_nested():
    # WHY: the fields stage cited a number the investigation report had not been checked for, and a finished
    # outlook was refused at publication. The check needs every list, not only the top-level ones.
    from edge_analysis_v2.analysis.service import listed_references
    response = {'factors': [{'sentence': 'f9는 문장 안에서는 번호가 아니에요', 'tool_run_ids': ['f1']}],
                'conclusion': {'supports': [{'label': 'x', 'tool_run_ids': ['f3', 'f7']}], 'burdens': []},
                'issue_detail': {'items': [{'tool_run_ids': ['f12']}]}}
    assert sorted(listed_references(response)) == ['f1', 'f12', 'f3', 'f7']
