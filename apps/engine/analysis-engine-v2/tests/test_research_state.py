"""A revision retains useful research; omission must never erase it."""
import pytest
import json

from edge_analysis_v2.analysis.research_state import ResearchState


def test_omission_keeps_initial_and_revision_material_without_mutating_parent():
    first = ResearchState()
    first.add('a', {'text': 'initial original'})
    saved = first.snapshot()
    second = ResearchState(saved)
    second.add('b', {'text': 'revision original'})
    third = ResearchState(second.snapshot())
    assert third.snapshot()['materials'] == {'a': {'text': 'initial original'}, 'b': {'text': 'revision original'}}
    assert saved['materials'] == {'a': {'text': 'initial original'}}


def test_explicit_removal_stays_removed_even_when_same_material_is_read_again():
    state = ResearchState()
    state.add('a', {'text': 'superseded'})
    state.add('b', {'text': 'corrected'})
    state.remove([{'id': 'a', 'reason': 'corrected disclosure', 'replacement_id': 'b'}])
    next_state = ResearchState(state.snapshot())
    next_state.add('a', {'text': 'superseded'})
    assert next_state.snapshot()['materials'] == {'b': {'text': 'corrected'}}
    assert next_state.snapshot()['removed']['a'] == {'reason': 'corrected disclosure', 'replacement_id': 'b'}


def test_invalid_removal_batch_does_not_partially_remove_material():
    state = ResearchState()
    state.add('a', {'text': 'keep'})
    before = state.snapshot()
    with pytest.raises(ValueError):
        state.remove([{'id': 'a', 'reason': 'old'}, {'id': 'unknown', 'reason': 'old'}])
    assert state.snapshot() == before


def test_material_identity_cannot_silently_replace_original_content():
    state = ResearchState()
    state.add('a', {'text': 'original'})
    state.add('a', {'text': 'original'})
    with pytest.raises(ValueError):
        state.add('a', {'text': 'different'})
    assert state.snapshot()['materials']['a']['text'] == 'original'


def test_invalid_state_version_is_not_silently_treated_as_first_analysis():
    with pytest.raises(ValueError):
        ResearchState({'version': 999, 'materials': {}, 'removed': {}})


@pytest.mark.parametrize('stage', ['', 'investigation', 'writing'])
def test_collector_preserves_large_originals_and_scopes_old_short_references(tmp_path, stage):
    from edge_analysis_v2.storage.research import collect_research
    original = 'original ' * 150000
    folder = tmp_path / stage
    folder.mkdir(exist_ok=True)
    (folder / 'workspace.json').write_text(json.dumps({'todos': [],
        'notes': {'reason.md': original + '[f1]'}, 'external_research': [
            {'tool': 'mcp__search__web_search_exa', 'arguments': {'query': 'q'}, 'output': {'text': 'search original'}}]}), encoding='utf-8')
    parent = {'analysis_id': 'a', 'analysis_at': '2026-10-16T00:00:00+00:00', 'etf_code': 'X', 'data_source': 'synthetic'}
    stored = {'tables': {'movement_analyses': [parent], 'tool_runs': [], 'tool_definitions': []}}
    result = collect_research(ResearchState(), stored, tmp_path, {'f1': 'old-run-id'})
    note = next(m for m in result['materials'].values() if m['kind'] == 'note')
    assert note['text'] == original + '[f1]'
    assert note['reference_ids'] == {'f1': 'old-run-id'}
    assert any(m['kind'] == 'external_search' and m['output']['text'] == 'search original' for m in result['materials'].values())
