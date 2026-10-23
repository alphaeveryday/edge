import json
import pytest
from edge_analysis_v2.contracts.response_errors import ResponseError
from edge_analysis_v2.contracts.publication_validation import movement_selection
from edge_analysis_v2.agent.output_schema import merge_outlook_revision


def test_forbidden_citations_identify_the_property_to_remove():
    with pytest.raises(ResponseError) as caught:
        merge_outlook_revision({'summary_card': {'title': 'before'}}, {'text_replacements': [
            {'field': 'summary_card.title', 'old_text': 'before', 'new_text': 'after', 'tool_run_ids': ['f1']}]})
    issue = json.loads(str(caught.value))
    assert issue['path'] == '$.text_replacements[0].tool_run_ids'
    assert issue['target'] == 'summary_card.title'
    assert issue['rule'] == 'forbidden'
    assert issue['repair'] == 'Remove this property.'


def test_mistyped_selection_lists_actual_choices_before_publication():
    response = {'new_items': [{'candidate_id': 'skt-aidc-plan-1012'}], 'selected_item_ids': ['skt-aidc-plan-0121']}
    with pytest.raises(ResponseError) as caught:
        movement_selection(response, ['earlier-today'])
    issue = caught.value.issue
    assert issue['path'] == '$.selected_item_ids[0]'
    assert issue['actual'] == 'skt-aidc-plan-0121'
    assert issue['expected'] == ['earlier-today', 'skt-aidc-plan-1012']
    response['selected_item_ids'] = issue['expected']
    movement_selection(response, ['earlier-today'])


@pytest.mark.parametrize('candidates,existing', [(['duplicate', 'duplicate'], []), (['existing'], ['existing'])])
def test_candidate_ids_cannot_ambiguously_resolve(candidates, existing):
    with pytest.raises(ResponseError):
        movement_selection({'new_items': [{'candidate_id': c} for c in candidates], 'selected_item_ids': []}, existing)
