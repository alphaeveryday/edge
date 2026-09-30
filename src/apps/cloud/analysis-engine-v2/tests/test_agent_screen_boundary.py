"""The model decides content once; the server owns screen-derived fields."""
from jsonschema import Draft202012Validator

from edge_analysis_v2.agent.output_schema import MOVEMENT, OUTLOOK, EDIT_SCHEMAS


def test_issue_detail_does_not_ask_model_to_repeat_parent_sticker():
    schema = OUTLOOK['properties']['issue_detail']
    value = {'headline': '공급 일정을 확인해요.', 'items': []}
    Draft202012Validator(schema).validate(value)
    assert set(schema['properties']) == {'headline', 'items'}
    assert not Draft202012Validator(schema).is_valid(value | {'sticker': '상승'})


def test_screen_metadata_and_cards_are_not_model_outputs():
    assert set(MOVEMENT['properties']) == {'new_items', 'selected_item_ids', 'summary'}
    assert set(OUTLOOK['properties']) == {'outlook', 'summary_card', 'factors', 'conclusion', 'issue_detail'}
    write = next(s['function']['parameters'] for s in EDIT_SCHEMAS if s['function']['name'] == 'write_outlook_body')
    topic = write['properties']['items']['items']
    assert topic['properties']['sentences']['items']['type'] == 'string'
    assert 'is_updated' not in topic['properties']
