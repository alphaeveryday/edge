"""Workspace notes and unfinished work survive without expanding filesystem access."""
import asyncio
import json
import pytest
from edge_analysis_v2.agent.work_session import WorkSession


def make(tmp_path):
    return WorkSession(tmp_path/'work', tmp_path/'artifacts', 'outlook', [])


def test_notes_can_create_folders_but_not_escape_or_replace_instructions(tmp_path):
    state = make(tmp_path)
    path = state.note_path('notes/company/answer.md', create=True)
    path.write_text('Current evidence', encoding='utf-8')
    state.checkpoint()
    assert json.loads((tmp_path/'artifacts/workspace.json').read_text())['notes']['company/answer.md'] == 'Current evidence'
    for bad in ('../secret.md', 'AGENTS.md', 'plugin/skills/x.md', 'notes/../../secret.md', 'notes/key.txt'):
        with pytest.raises(ValueError):
            state.note_path(bad, create=True)


def test_pending_work_gets_specific_continuation_then_fails_instead_of_publishing(tmp_path):
    state = make(tmp_path)
    state.update_tasks([{'content':'Check order margins', 'status':'in_progress'}])
    for _ in range(3):
        assert 'Check order margins' in state.continuation()
    with pytest.raises(ValueError, match='Unfinished'):
        state.continuation()
    assert json.loads((tmp_path/'artifacts/workspace.json').read_text())['status'] == 'incomplete'


def test_failed_todo_cannot_erase_open_work(tmp_path):
    state = make(tmp_path)
    state.todos = [{'content':'Open question', 'status':'pending', 'activeForm':'Researching'}]
    with pytest.raises(ValueError):
        state.update_tasks([{'content':'', 'status':'completed'}])
    assert state.todos


def test_native_write_hook_denies_instruction_replacement_and_allows_notes(tmp_path):
    state = make(tmp_path)
    for path, expected in [('AGENTS.md', 'deny'), ('notes/sub/answer.md', 'allow'),
                           ('notes/../../outside.md', 'deny'), ('notes/run.py', 'deny')]:
        result = asyncio.run(state.before({'tool_name':'Write',
            'tool_input':{'file_path':path, 'content':'Evidence'}}, 'write', {}))
        assert result['hookSpecificOutput']['permissionDecision'] == expected


def test_mid_run_reminder_is_bounded_and_names_open_question(tmp_path):
    state = make(tmp_path)
    state.todos = [{'content':'Explain margin decline', 'status':'pending'}]
    reminders = []
    for _ in range(48):
        value = asyncio.run(state.after({'tool_name':'mcp__analysis__search_web',
            'tool_input':{}, 'tool_response':{}}, 'search', {}))
        if value:
            reminders.append(value['hookSpecificOutput']['additionalContext'])
    assert len(reminders) == 2
    assert all('Explain margin decline' in value for value in reminders)


def test_interrupted_notes_and_tasks_resume_in_a_fresh_workspace(tmp_path):
    state = make(tmp_path)
    state.note_path('notes/findings.md', create=True).write_text('IR contradicts order headline', encoding='utf-8')
    state.todos = [{'content':'Reflect IR in conclusion', 'status':'pending', 'activeForm':'Revising'}]
    state.status = 'interrupted'
    state.checkpoint()
    resumed = WorkSession(tmp_path/'fresh', tmp_path/'artifacts', 'outlook', [])
    summary = resumed.restore()
    assert summary['todos'][0]['content'] == 'Reflect IR in conclusion'
    assert resumed.note_path('notes/findings.md').read_text() == 'IR contradicts order headline'
    assert 'Reflect IR' in resumed.continuation()


def test_compaction_saves_notes_and_restores_context_before_next_research_tool(tmp_path):
    state = make(tmp_path)
    state.analysis_tools = frozenset({'mcp__analysis__search_web'})
    state.note_path('notes/index.md', create=True).write_text('Margin contradiction: read findings.md', encoding='utf-8')
    state.todos = [{'content':'Find actual margins', 'status':'pending'}]
    asyncio.run(state.precompact({'trigger':'auto'}, None, {}))
    snapshot = json.loads((tmp_path/'artifacts/workspace.json').read_text())
    assert snapshot['notes']['index.md'].startswith('Margin contradiction')
    # An attempted forbidden call must not consume the recovery reminder.
    asyncio.run(state.before({'tool_name':'Write', 'tool_input':{'file_path':'AGENTS.md'}}, 'bad', {}))
    result = asyncio.run(state.before({'tool_name':'mcp__analysis__search_web', 'tool_input':{}}, 'search', {}))
    context = result['hookSpecificOutput']['additionalContext']
    assert 'Find actual margins' in context and 'Margin contradiction' in context
    assert 'AGENTS.md' in context
    assert state.recovery_context is None


def test_compacted_agent_cannot_publish_without_recovery_even_with_empty_todos(tmp_path):
    state = make(tmp_path)
    asyncio.run(state.precompact({'trigger':'auto'}, None, {}))
    result = asyncio.run(state.before({'tool_name':'StructuredOutput', 'tool_input':{}}, 'final', {}))
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert state.recovery_context is not None
    continuation = state.continuation()
    assert 'AGENTS.md' in continuation
    assert state.status == 'running'
    assert state.continuation() is not None


def test_unregistered_questions_cannot_be_treated_as_completed_research(tmp_path):
    state = make(tmp_path)
    for _ in range(3):
        assert 'workspace.update_tasks' in state.continuation()
        assert state.status == 'running'
    with pytest.raises(ValueError, match='Unfinished'):
        state.continuation()
    assert state.status == 'incomplete'


def test_first_research_call_requires_questions_but_registration_remains_available(tmp_path):
    state = make(tmp_path)
    state.analysis_tools = frozenset({'mcp__analysis__search_web', 'mcp__workspace__update_tasks'})
    for tool, expected in [('mcp__analysis__search_web', 'deny'), ('StructuredOutput', 'deny'),
                           ('mcp__workspace__update_tasks', 'allow')]:
        result = asyncio.run(state.before({'tool_name':tool, 'tool_input':{}}, 'test', {}))
        assert result['hookSpecificOutput']['permissionDecision'] == expected
    state.update_tasks([{'content':'Does the new order affect this quarter profit?', 'status':'pending'}])
    result = asyncio.run(state.before({'tool_name':'mcp__analysis__search_web', 'tool_input':{}}, 'search', {}))
    assert result['hookSpecificOutput']['permissionDecision'] == 'allow'


def test_empty_replacement_cannot_erase_registered_questions(tmp_path):
    state = make(tmp_path)
    state.update_tasks([{'content':'Can shipments happen this quarter?', 'status':'pending'}])
    with pytest.raises(ValueError):
        state.update_tasks([])
    assert state.todos[0]['content'] == 'Can shipments happen this quarter?'


def test_recovery_context_is_delivered_once_and_survives_a_directory_named_like_the_index(tmp_path):
    import asyncio
    workspace, artifacts = tmp_path/'w', tmp_path/'a'
    workspace.mkdir(); artifacts.mkdir()
    session = WorkSession(workspace, artifacts, 'outlook', ['mcp__analysis__x'])
    session.update_tasks([{'content': 'What changed?', 'status': 'pending'}])
    session.note_path('notes/index.md', create=True, directory=True)   # a folder where the index file is expected
    asyncio.run(session.precompact({'trigger': 'auto'}, None, None))
    assert session.recovery_context and 'What changed?' in session.recovery_context
    denied = asyncio.run(session.before({'tool_name': 'StructuredOutput', 'tool_input': {}}, None, None))['hookSpecificOutput']
    assert denied['permissionDecision'] == 'deny' and 'Context compaction' in denied['permissionDecisionReason']
    again = asyncio.run(session.before({'tool_name': 'StructuredOutput', 'tool_input': {}}, None, None))['hookSpecificOutput']
    assert again['permissionDecision'] == 'allow' and session.recovery_context is None
