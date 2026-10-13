from edge_analysis_v2.analysis.service import swap_references


def test_short_references_become_execution_ids_wherever_evidence_is_listed():
    table = {'f1': 'a' * 32, 'f2': 'b' * 32}
    written = {'items': [{'id': 't1', 'sentences': ['f1은 문장 안에서는 그대로'], 'tool_run_ids': ['f2', 'f1']}],
               'conclusion': {'supports': [{'label': 'x', 'tool_run_ids': ['f1', 'c' * 32, 'f9']}]}}
    assert swap_references(written, table) == {
        'items': [{'id': 't1', 'sentences': ['f1은 문장 안에서는 그대로'], 'tool_run_ids': ['b' * 32, 'a' * 32]}],
        # An inherited execution ID and an invented reference both reach the store unchanged.
        'conclusion': {'supports': [{'label': 'x', 'tool_run_ids': ['a' * 32, 'c' * 32, 'f9']}]}}
