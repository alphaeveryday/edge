"""Recoverable draft mistakes must not terminate a whole analysis."""
from datetime import datetime

import pytest

from edge_analysis_v2.analysis.body_editor import BodyEditor
from edge_analysis_v2.tools.execution import ToolInputError


@pytest.mark.parametrize('changes, message', [
    ([{'action':'remove', 'id':'missing'}], 'existing topic'),
    ([{'action':'update', 'id':'missing'}], 'Unknown topic'),
    ([{'action':'remove', 'id':'one'}, {'action':'remove', 'id':'one'}], 'Duplicate topic'),
])
def test_invalid_edit_is_correctable_and_does_not_mutate_the_draft(changes, message):
    editor = BodyEditor(None, datetime.fromisoformat('2026-10-18T08:30:00+09:00'))
    before = editor.write('Title', [{'id':'one', 'title_keyword':'Topic', 'sentences':['Text'],
                                   'sentiment':'neutral', 'tool_run_ids':['ref']}])
    with pytest.raises(ToolInputError, match=message):
        editor.apply(changes)
    assert editor.draft['items'] == before['items']
    assert editor.apply([{'action':'remove', 'id':'one'}])['items'] == []


def test_first_draft_wrong_tool_can_recover_by_writing():
    editor = BodyEditor(None, datetime.fromisoformat('2026-10-18T08:30:00+09:00'))
    with pytest.raises(ToolInputError, match='write'):
        editor.apply([])
    assert editor.write('Title', [])['title'] == 'Title'
