"""Exact text edits retain unrelated conditions through audited publication."""
import pytest

from test_service_outlook_revision_fields import revisions
from test_service_failure_boundaries import final_response
from edge_analysis_v2.tools.execution import ToolExecutionError


def test_text_replacement_preserves_conditions_and_rejected_evidence_leaves_draft_intact(revisions):
    async def initial(**kwargs):
        ref = kwargs['call']('get_news_evidence', {'news_ids': [kwargs['initial']['news'][0]['news_id']],
                                                   'include_body': False})['tool_run_id']
        kwargs['call']('write_outlook_body', {'title': '실적 일정', 'items': [
            {'id': 'dates', 'title_keyword': '발표를 확인해요',
             'sentences': ['코스맥스와 한국콜마의 실적 발표일은 미정이에요.', '마진도 확인해야 해요.'],
             'sentiment': 'neutral', 'tool_run_ids': [ref]}]})
        return final_response(ref)
    parent, screen, _ = revisions(initial)
    async def revision(**kwargs):
        old = kwargs['initial']['previous_analysis']['detail']['items'][0]
        search = kwargs['call']('search_news_threads', {})['tool_run_id']
        change = {'action': 'update', 'id': 'dates', 'sentiment': 'neutral', 'tool_run_ids': [search],
                  'text_replacements': [{'sentence_number': 1, 'old_text': '코스맥스와 ',
                                         'new_text': '코스맥스는 11월 5일 발표하고 '}]}
        with pytest.raises(ToolExecutionError, match='not final-eligible'):
            kwargs['call']('apply_outlook_body_changes', {'changes': [change]})
        draft = kwargs['call']('apply_outlook_body_changes', {'changes': []})['result']
        assert draft['items'][0]['sentences'] == old['sentences']
        change['tool_run_ids'] = old['tool_run_ids']
        kwargs['call']('apply_outlook_body_changes', {'changes': [change]})
        return {}
    _, result, _ = revisions(revision, parent)
    assert result['detail']['items'][0]['sentences'] == [
        {'sentence': '코스맥스는 11월 5일 발표하고 한국콜마의 실적 발표일은 미정이에요.', 'is_updated': True},
        {'sentence': '마진도 확인해야 해요.', 'is_updated': False}]
    assert result['summary_card'] == screen['summary_card']
    assert result['conclusion'] == screen['conclusion']
