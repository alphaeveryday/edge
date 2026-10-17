"""A date correction must retain another company's financial condition."""
from copy import deepcopy

import pytest

from edge_analysis_v2.agent.output_schema import merge_outlook_revision


def previous():
    return {'summary_card': {'title': '기존 제목', 'summary': '예정이에요.'},
            'issue_detail': {'headline': '실적', 'items': [
                {'title_keyword': '실적 발표', 'sentence': '코스맥스 발표일은 미정이고 한국콜마 918억원 확인이 필요해요.',
                 'sentiment': 'neutral', 'tool_run_ids': ['old']},
                {'title_keyword': '다른 사건', 'sentence': '그대로 유지해요.', 'sentiment': 'positive', 'tool_run_ids': ['other']}]}}


def edit(**values):
    return {'field': 'issue_detail.items.sentence', 'item_number': 1,
            'old_text': '코스맥스 발표일은 미정', 'new_text': '코스맥스 발표일은 11월 5일',
            'tool_run_ids': ['old', 'new'], **values}


def test_only_target_phrase_and_its_explicit_evidence_change():
    base = previous()
    before = deepcopy(base)
    result = merge_outlook_revision(base, {'text_replacements': [edit()]})
    assert result['issue_detail']['items'][0]['sentence'] == '코스맥스 발표일은 11월 5일이고 한국콜마 918억원 확인이 필요해요.'
    assert result['issue_detail']['items'][0]['tool_run_ids'] == ['old', 'new']
    assert result['issue_detail']['items'][1] == base['issue_detail']['items'][1]
    assert result['summary_card'] == base['summary_card']
    assert base == before
    assert 'text_replacements' not in result


@pytest.mark.parametrize('extra', [{'issue_detail': {'items': []}}, {'issue_detail': {'items': previous()['issue_detail']['items']}}])
def test_parent_array_submission_conflicts_with_text_replacement(extra):
    with pytest.raises(ValueError, match='conflict'):
        merge_outlook_revision(previous(), extra | {'text_replacements': [edit()]})


def test_same_scalar_submission_conflicts_but_sibling_does_not():
    replacement = {'field': 'summary_card.summary', 'old_text': '예정', 'new_text': '확정'}
    with pytest.raises(ValueError, match='conflict'):
        merge_outlook_revision(previous(), {'summary_card': {'summary': '다른 문장'}, 'text_replacements': [replacement]})
    assert merge_outlook_revision(previous(), {'summary_card': {'title': '새 제목'}, 'text_replacements': [replacement]})['summary_card'] == {
        'title': '새 제목', 'summary': '확정이에요.'}


@pytest.mark.parametrize('change', [edit(item_number=True), edit(item_number=0), edit(item_number=3), edit(field='outlook.direction'),
    edit(old_text='없는 원문'), edit(tool_run_ids=[]), {key: value for key, value in edit().items() if key != 'tool_run_ids'}])
def test_invalid_target_or_missing_evidence_rejected(change):
    with pytest.raises(ValueError):
        merge_outlook_revision(previous(), {'text_replacements': [change]})


def test_duplicate_target_and_missing_legacy_target_rejected():
    with pytest.raises(ValueError):
        merge_outlook_revision(previous(), {'text_replacements': [edit(), edit()]})
    with pytest.raises(ValueError):
        merge_outlook_revision({}, {'text_replacements': [edit()]})


def test_same_items_citation_lists_must_agree_and_nonreferenced_fields_cannot_accept_them():
    title = edit(field='issue_detail.items.title_keyword', old_text='실적', new_text='일정', tool_run_ids=['different'])
    with pytest.raises(ValueError, match='Conflicting tool_run_ids'):
        merge_outlook_revision(previous(), {'text_replacements': [edit(), title]})
    title['tool_run_ids'] = edit()['tool_run_ids']
    result = merge_outlook_revision(previous(), {'text_replacements': [edit(), title]})
    assert result['issue_detail']['items'][0]['title_keyword'] == '일정 발표'
    with pytest.raises(ValueError, match='does not have tool_run_ids'):
        merge_outlook_revision(previous(), {'text_replacements': [
            {'field': 'summary_card.summary', 'old_text': '예정', 'new_text': '확정', 'tool_run_ids': ['run']}]})


def test_keyword_and_factor_text_are_editable_but_labels_and_sentiment_are_not():
    base = {'conclusion': {'supports': [{'label': '예정', 'tool_run_ids': ['old']}]},
            'factors': [{'type': '이슈', 'sticker': '중립', 'sentence': '예정이에요.'}]}
    result = merge_outlook_revision(base, {'text_replacements': [
        {'field': 'conclusion.supports.label', 'item_number': 1, 'old_text': '예정', 'new_text': '확정', 'tool_run_ids': ['old', 'new']},
        {'field': 'factors.sentence', 'item_number': 1, 'old_text': '예정', 'new_text': '확정'}]})
    assert result['factors'] == [{'type': '이슈', 'sticker': '중립', 'sentence': '확정이에요.'}]
    assert result['conclusion']['supports'] == [{'label': '확정', 'tool_run_ids': ['old', 'new']}]
    with pytest.raises(ValueError):
        merge_outlook_revision(base, {'text_replacements': [{'field': 'factors.sticker', 'item_number': 1, 'old_text': '중립', 'new_text': '상승'}]})


def test_only_revision_schema_accepts_text_replacements():
    from jsonschema import Draft202012Validator, ValidationError
    from edge_analysis_v2.agent.output_schema import OUTLOOK, OUTLOOK_REVISION, MOVEMENT
    Draft202012Validator(OUTLOOK_REVISION).validate({'text_replacements': [edit()]})
    assert 'text_replacements' not in OUTLOOK['properties']
    assert 'text_replacements' not in MOVEMENT['properties']
    for value in ({'text_replacements': []}, {'text_replacements': [edit(item_number=True)]},
                  {'text_replacements': [edit(field='issue_detail.items.tool_run_ids')]}):
        with pytest.raises(ValidationError):
            Draft202012Validator(OUTLOOK_REVISION).validate(value)
