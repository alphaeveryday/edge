"""The model decides content once; the server owns screen-derived fields."""
from jsonschema import Draft202012Validator
import pytest

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


@pytest.mark.parametrize('schema', [
    OUTLOOK['properties']['summary_card']['properties']['title'],
    OUTLOOK['properties']['issue_detail']['properties']['headline'],
    OUTLOOK['properties']['issue_detail']['properties']['items']['items']['properties']['title_keyword'],
    MOVEMENT['properties']['new_items']['items']['properties']['title_keyword'],
    EDIT_SCHEMAS[0]['function']['parameters']['properties']['title'],
    EDIT_SCHEMAS[0]['function']['parameters']['properties']['items']['items']['properties']['title_keyword'],
])
def test_customer_titles_fit_twenty_characters_including_spaces_and_punctuation(schema):
    # Long explanations belong in the body, including for issue and movement titles.
    validator = Draft202012Validator(schema)
    title = '가나다라 바사아자.' * 2
    assert len(title) == 20
    validator.validate(title)
    assert not validator.is_valid(title + '요')


def test_topic_update_flags_are_rejected_while_bullet_updates_remain_available():
    schema = EDIT_SCHEMAS[0]['function']['parameters']['properties']['items']['items']
    topic = {'id': 't1', 'title_keyword': '수주 증가', 'sentences': ['새 계약이 늘었어요.'],
             'sentiment': 'positive', 'tool_run_ids': ['r1'], 'updated_sentence_numbers': [1]}
    validator = Draft202012Validator(schema)
    validator.validate(topic)
    assert not validator.is_valid(topic | {'is_updated': True})
