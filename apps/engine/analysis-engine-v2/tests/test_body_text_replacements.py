"""Correct one clause without losing the other company's still-valid condition."""
import pytest

from edge_analysis_v2.analysis.body_editor import BodyEditor
from test_body_changes import NOW, topic


def editor():
    original = BodyEditor(None, NOW)
    original.write('일정 확인', [topic(sentence='코스맥스와 한국콜마의 실적 발표일은 미정이에요.'), topic('b', '다른 조건은 유지해요.')])
    return BodyEditor(original.result(), NOW)


def change(**extra):
    return {'action': 'update', 'id': 'a', 'sentiment': 'positive', 'tool_run_ids': ['run-1'],
            'text_replacements': [{'sentence_number': 1, 'old_text': '코스맥스와 한국콜마의 실적 발표일은 미정이에요.',
                                   'new_text': '코스맥스는 11월 5일 발표하고 한국콜마의 발표일은 미정이에요.'}], **extra}


def test_exact_clause_replacement_preserves_other_condition_and_topic():
    draft = editor()
    before = draft.result()
    update = change(text_replacements=[{'sentence_number': 1, 'old_text': '코스맥스와 ',
                                       'new_text': '코스맥스는 11월 5일 발표하고 '}])
    result = draft.apply([update])
    assert result['items'][0]['sentences'] == [{'sentence': '코스맥스는 11월 5일 발표하고 한국콜마의 실적 발표일은 미정이에요.', 'is_updated': True}]
    assert result['items'][1] == before['items'][1]


@pytest.mark.parametrize('replacement', [[], [{'sentence_number': True, 'old_text': '코스맥스', 'new_text': '수정'}],
    [{'sentence_number': 0, 'old_text': '코스맥스', 'new_text': '수정'}],
    [{'sentence_number': 2, 'old_text': '코스맥스', 'new_text': '수정'}],
    [{'sentence_number': 1, 'old_text': '', 'new_text': '수정'}],
    [{'sentence_number': 1, 'old_text': '없는 원문', 'new_text': '수정'}],
    [{'sentence_number': 1, 'old_text': '코스맥스', 'new_text': '코스맥스'}],
    [{'sentence_number': 1, 'old_text': '코스맥스', 'new_text': None}],
    [{'sentence_number': 1, 'old_text': '코스맥스', 'new_text': '수정'}] * 2])
def test_invalid_replacement_rejects_entire_batch(replacement):
    draft = editor()
    before = draft.result()
    with pytest.raises(ValueError):
        draft.apply([{'action': 'remove', 'id': 'b'}, change(text_replacements=replacement)])
    assert draft.result() == before


@pytest.mark.parametrize('field', ['sentences', 'updated_sentence_numbers'])
def test_replacement_cannot_mix_with_full_sentence_submission(field):
    draft = editor()
    with pytest.raises(ValueError):
        draft.apply([change(**{field: []})])


def test_ambiguous_match_and_missing_evidence_or_sentiment_are_rejected():
    draft = editor()
    duplicate = change(text_replacements=[{'sentence_number': 1, 'old_text': ' ', 'new_text': '-'}])
    with pytest.raises(ValueError):
        draft.apply([duplicate])
    for field in ('tool_run_ids', 'sentiment'):
        update = change()
        del update[field]
        with pytest.raises(ValueError):
            draft.apply([update])
    with pytest.raises(ValueError):
        draft.apply([change(text_replacements=[{'sentence_number': 1, 'old_text': '코스맥스', 'new_text': 'x' * 2000}])])


def test_overlapping_matches_are_ambiguous_too():
    original = BodyEditor(None, NOW)
    original.write('title', [topic(sentence='aaa')])
    draft = BodyEditor(original.result(), NOW)
    with pytest.raises(ValueError, match='exactly once'):
        draft.apply([change(text_replacements=[{'sentence_number': 1, 'old_text': 'aa', 'new_text': 'b'}])])


def test_tool_schema_exposes_replacement_but_first_write_schema_does_not():
    from jsonschema import Draft202012Validator, ValidationError
    from edge_analysis_v2.agent.output_schema import EDIT_SCHEMAS
    write, apply = [schema['function']['parameters'] for schema in EDIT_SCHEMAS]
    validator = Draft202012Validator(apply)
    validator.validate({'changes': [change()]})
    with pytest.raises(ValidationError):
        validator.validate({'changes': [change(text_replacements=[])]})
    with pytest.raises(ValidationError):
        validator.validate({'changes': [change(text_replacements=[{'sentence_number': True, 'old_text': 'old', 'new_text': 'new'}])]})
    assert 'text_replacements' not in write['properties']['items']['items']['properties']


def test_replacement_matching_another_bullet_is_still_marked_as_updated():
    original = BodyEditor(None, NOW)
    item = topic()
    item['sentences'] = ['발표는 미정이에요.', '발표는 내일이에요.']
    original.write('title', [item])
    draft = BodyEditor(original.result(), NOW)
    result = draft.apply([change(text_replacements=[{'sentence_number': 1, 'old_text': '미정', 'new_text': '내일'}])])
    assert result['items'][0]['sentences'] == [
        {'sentence': '발표는 내일이에요.', 'is_updated': True},
        {'sentence': '발표는 내일이에요.', 'is_updated': False}]
    assert result['updates']['items'][0]['sentence'] == '발표는 내일이에요.'


def test_two_explicit_replacements_require_sentiment_and_record_both_positions_even_if_texts_swap():
    original = BodyEditor(None, NOW)
    item = topic()
    item['sentences'] = ['매출은 늘어요.', '매출은 줄어요.']
    original.write('title', [item])
    draft = BodyEditor(original.result(), NOW)
    update = change(text_replacements=[{'sentence_number': 1, 'old_text': '늘', 'new_text': '줄'},
                                      {'sentence_number': 2, 'old_text': '줄', 'new_text': '늘'}])
    without_sentiment = {key: value for key, value in update.items() if key != 'sentiment'}
    with pytest.raises(ValueError, match='sentiment'):
        draft.apply([without_sentiment])
    result = draft.apply([update])
    assert all(sentence['is_updated'] for sentence in result['items'][0]['sentences'])
    assert result['updates']['items'][0]['sentence'] == '매출은 줄어요.\n매출은 늘어요.'


def test_later_replacement_keeps_untouched_duplicate_bullets_update_flags():
    original = BodyEditor(None, NOW)
    item = topic()
    item['sentences'] = ['A', 'B']
    original.write('title', [item])
    draft = BodyEditor(original.result(), NOW)
    draft.apply([change(text_replacements=[{'sentence_number': 2, 'old_text': 'B', 'new_text': 'A'}])])
    result = draft.apply([change(text_replacements=[{'sentence_number': 1, 'old_text': 'A', 'new_text': 'C'}])])
    assert result['items'][0]['sentences'] == [
        {'sentence': 'C', 'is_updated': True}, {'sentence': 'A', 'is_updated': True}]
    assert result['updates']['items'][0]['sentence'] == 'C\nA'
