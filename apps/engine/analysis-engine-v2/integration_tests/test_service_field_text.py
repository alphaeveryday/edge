"""Final issue corrections preserve unrelated facts and retain evidence validation."""
from copy import deepcopy

from test_service_outlook_revision_fields import revisions
from test_service_failure_boundaries import final_response, write_body


def test_issue_phrase_correction_preserves_kolmar_condition_and_rejects_bad_evidence(revisions):
    async def initial(**kwargs):
        response = final_response(write_body(kwargs))
        response['issue_detail']['items'][0]['sentence'] = '코스맥스 발표일은 미정이고 한국콜마 918억원 확인이 필요해요.'
        return response
    parent, screen, issue = revisions(initial)
    async def revision(**kwargs):
        search = kwargs['call']('search_news_threads', {})['tool_run_id']
        edit = {'field': 'issue_detail.items.sentence', 'item_number': 1,
                'old_text': '코스맥스 발표일은 미정', 'new_text': '코스맥스 발표일은 11월 5일',
                'tool_run_ids': [search]}
        assert '최종 근거로 쓸 수 없습니다' in kwargs['final_check']({'text_replacements': [edit]})
        invalid = deepcopy(edit)
        invalid['old_text'] = '없는 원문'
        assert 'exactly once' in kwargs['final_check']({'text_replacements': [invalid]})
        assert 'conflict' in kwargs['final_check']({'issue_detail': {'items': []}, 'text_replacements': [edit]})
        ref = kwargs['call']('get_issue_evidence', {'news_ids': [kwargs['initial']['news'][0]['news_id']],
                                                   'include_body': False})['tool_run_id']
        edit['tool_run_ids'] = issue['items'][0]['tool_run_ids'] + [ref]
        result = {'text_replacements': [edit]}
        assert kwargs['final_check'](result) is None
        return result
    _, revised, updated_issue = revisions(revision, parent)
    assert revised == screen
    assert updated_issue['items'][0]['sentence'] == '코스맥스 발표일은 11월 5일이고 한국콜마 918억원 확인이 필요해요.'
    assert set(issue['items'][0]['tool_run_ids']) < set(updated_issue['items'][0]['tool_run_ids'])
