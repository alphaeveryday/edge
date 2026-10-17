"""A revision retains useful research; omission must never erase it."""
import pytest
import json

from edge_analysis_v2.analysis.research_state import ResearchState


def test_material_order_survives_jsonb_sorting_and_appends_only_new_active_originals():
    state = ResearchState()
    state.add('z', {'text': 'first original'})
    state.add('m', {'text': 'second original'})
    saved = json.loads(json.dumps(state.snapshot(), sort_keys=True))
    restored = ResearchState(saved)
    restored.add('a', {'text': 'new original'})
    restored.add('duplicate', {'text': 'first original'})
    assert restored.snapshot()['material_order'] == ['z', 'm', 'a']
    restored.remove([{'id': 'm', 'reason': 'superseded'}])
    restored.add('reread', {'text': 'second original'})
    assert restored.snapshot()['material_order'] == ['z', 'a']
    assert saved['materials'] == {'m': {'text': 'second original'}, 'z': {'text': 'first original'}}


def test_legacy_state_without_order_keeps_every_original_in_a_deterministic_order():
    state = ResearchState()
    state.add('z', {'text': 'last'})
    state.add('a', {'text': 'first'})
    legacy = state.snapshot()
    legacy.pop('material_order', None)
    migrated = ResearchState(legacy).snapshot()
    assert migrated['material_order'] == ['a', 'z']
    assert migrated['materials'] == legacy['materials']


@pytest.mark.parametrize('order', [['a', 'a'], [None], 'a'])
def test_corrupt_material_order_is_reported_instead_of_hiding_originals(order):
    state = ResearchState()
    state.add('a', {'text': 'original'})
    value = state.snapshot() | {'material_order': order}
    with pytest.raises(ValueError, match='order'):
        ResearchState(value)


def test_old_writer_rollback_repairs_stale_order_without_resurrecting_or_losing_originals():
    state = ResearchState()
    state.add('z', {'text': 'keep'})
    state.add('m', {'text': 'retire'})
    saved = state.snapshot()
    # A previous version edits materials but preserves the unknown order field as-is.
    saved['materials'].pop('m')
    saved['removed']['m'] = {'reason': 'old writer explicitly retired'}
    saved['materials']['a'] = {'text': 'old writer added'}
    with pytest.warns(RuntimeWarning, match='order was stale'):
        restored = ResearchState(saved).snapshot()
    assert restored['material_order'] == ['z', 'a']
    assert restored['materials'] == saved['materials']
    assert restored['removed'] == saved['removed']


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


@pytest.mark.parametrize('version', [1, 999])
def test_invalid_state_version_is_not_silently_treated_as_first_analysis(version):
    with pytest.raises(ValueError):
        ResearchState({'version': version, 'materials': {}, 'removed': {}, 'gaps': []})


def test_retirement_resolves_duplicate_reference_and_replacement_without_mutating_request():
    state = ResearchState()
    for identity, text in [('a', 'old'), ('a2', 'old'), ('b', 'new'), ('b2', 'new')]:
        state.add(identity, {'text': text})
    changes = [{'id': 'a2', 'reason': 'corrected', 'replacement_id': 'b2'}]
    state.remove(changes)
    assert state.snapshot()['materials'] == {'b': {'text': 'new'}}
    assert state.snapshot()['removed']['a']['replacement_id'] == 'b'
    assert changes[0]['id'] == 'a2'


def test_duplicate_alias_removals_fail_atomically():
    state = ResearchState()
    state.add('a', {'text': 'old'})
    state.add('alias', {'text': 'old'})
    before = state.snapshot()
    with pytest.raises(ValueError):
        state.remove([{'id': 'a', 'reason': 'old'}, {'id': 'alias', 'reason': 'old'}])
    assert state.snapshot() == before


def test_same_original_under_a_new_tool_run_id_does_not_resurrect_retired_material():
    def observation(run, at):
        return {'kind': 'tool', 'definition': {'function_name': 'get_issue_evidence'},
                'arguments': {'news_ids': ['a']}, 'context': {'analysis_at': at, 'flow_as_of_date': at},
                'output': {'tool_run_id': run, 'result': {'news': [{'news_id': 'a', 'body': 'original'}]}}}
    state = ResearchState()
    state.add('run-a', observation('run-a', '2026-10-15'))
    state.remove([{'id': 'run-a', 'reason': 'excluded'}])
    next_state = ResearchState(state.snapshot())
    next_state.add('run-b', observation('run-b', '2026-10-16'))
    assert next_state.snapshot()['materials'] == {}
    assert next_state.snapshot()['aliases']['run-b'] == 'run-a'


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
