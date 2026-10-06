"""Document skills must never expand the worker's filesystem or tool authority."""
import asyncio
import json
from pathlib import Path

import pytest

from edge_analysis_v2.agent.skill_session import SkillSession


def hook(session, name, args):
    return asyncio.run(session.before(dict(tool_name=name, tool_input=args), 'call', {}))


def allowed(result):
    return result['hookSpecificOutput']['permissionDecision'] == 'allow'


def session(tmp_path):
    return SkillSession(tmp_path/'workspace', tmp_path/'artifacts', 'outlook', ['mcp__analysis__observe'])


def test_analysis_and_final_output_do_not_require_skill_calls(tmp_path):
    state = session(tmp_path)
    assert allowed(hook(state, 'mcp__analysis__observe', {}))
    assert allowed(hook(state, 'StructuredOutput', {}))
    for name in state.names:
        assert allowed(hook(state, 'Skill', {'skill':name}))
    record = json.loads((tmp_path/'artifacts'/'skills.json').read_text())
    assert set(record['available']) == set(state.names)
    assert 'loaded' not in record and 'required' not in record
    assert all(len(doc['sha256']) == 64 for doc in record['documents'])


@pytest.mark.parametrize('tool,args', [
    ('Bash', {'command':'cat /etc/passwd'}), ('Write', {'file_path':'notes.md'}),
    ('WebFetch', {'url':'https://example.com'}), ('Agent', {}),
    ('Skill', {'skill':'unapproved'}), ('mcp__analysis__unknown', {}),
    ('Read', {'file_path':'../secret'}), ('Read', {'file_path':'/etc/passwd'}),
])
def test_unknown_tools_and_paths_are_denied(tmp_path, tool, args):
    assert not allowed(hook(session(tmp_path), tool, args))


def test_read_allows_only_snapshotted_documents_not_neighbor_files(tmp_path):
    state = session(tmp_path)
    document = next(state.plugin.glob('skills/*/SKILL.md'))
    assert allowed(hook(state, 'Read', {'file_path':str(document)}))
    neighbor = document.with_name('secret.txt')
    neighbor.write_text('not a skill')
    assert not allowed(hook(state, 'Read', {'file_path':str(neighbor)}))
    document.chmod(0o600)
    document.write_text('modified instructions')
    assert allowed(hook(state, 'Read', {'file_path':str(document)}))
    assert allowed(hook(state, 'Skill', {'skill':state.names[0]}))


def test_workspace_instructions_are_readable_without_opening_neighbor_files(tmp_path):
    state = session(tmp_path)
    agents = state.workspace/'AGENTS.md'
    assert agents.is_file()
    assert allowed(hook(state, 'Read', {'file_path':str(agents)}))
