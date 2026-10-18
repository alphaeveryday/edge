"""A revision retains useful research; omission must never erase it."""
import pytest
import json

from edge_analysis_v2.analysis.research_state import ResearchState


def test_legacy_descendant_never_inherits_ancestor_workspace_as_current(monkeypatch):
    from edge_analysis_v2.storage import research
    from edge_analysis_v2.agent.work_context import WorkContext
    state = ResearchState()
    state.add('q', {'kind': 'questions', 'todos': [{'content': 'old question', 'status': 'pending'}]})
    state.value.update(analysis={'analysis_id': 'A'}, current_work={
        'analysis_id': 'A', 'stages': {'root': {'questions': 'q', 'notes': {}}}})
    def stored(identity, previous, at, runs):
        return {'tables': {'outlook_analyses': [{'analysis_id': identity, 'previous_analysis_id': previous,
            'analysis_at': at, 'status': 'completed', 'etf_code': 'X', 'data_source': 'synthetic'}],
            'tool_definitions': [research.DEFINITION], 'tool_runs': runs}}
    records = {
        'A': stored('A', None, '2026-10-16', [{'tool_id': research.DEFINITION['tool_id'],
            'status': 'completed', 'output': {'result': state.snapshot()}}]),
        'B': stored('B', 'A', '2026-10-17', [])}
    monkeypatch.setattr(research, 'read_storage', lambda connection, kind, identity: records[identity])
    restored = research.load_research(None, 'outlook', 'B').snapshot()
    assert restored['analysis']['analysis_id'] == 'B'
    assert 'current_work' not in restored
    assert restored['materials'] == state.snapshot()['materials']
    assert WorkContext({'research_state': restored}).initial['research_state']['current_work']['status'] == 'unavailable'


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
    work = result['current_work']
    assert work['analysis_id'] == 'a'
    assert set(work['stages']) == {stage or 'root'}
    pointers = work['stages'][stage or 'root']
    assert result['materials'][pointers['notes']['reason.md']] == note
    assert result['materials'][pointers['questions']] == {'kind': 'questions', 'todos': []}


def collected_work(state, folder, identity):
    from edge_analysis_v2.storage.research import collect_research
    parent = {'analysis_id': identity, 'analysis_at': '2026-10-16T00:00:00+00:00',
              'etf_code': 'X', 'data_source': 'synthetic'}
    stored = {'tables': {'outlook_analyses': [parent], 'tool_runs': [], 'tool_definitions': []}}
    return collect_research(state, stored, folder)


def test_current_work_tracks_return_to_an_older_note_without_reordering_originals(tmp_path):
    state = ResearchState()
    snapshots = []
    for identity, text in [('first', 'A'), ('second', 'B'), ('third', 'A')]:
        folder = tmp_path / identity
        folder.mkdir()
        (folder / 'workspace.json').write_text(json.dumps({
            'todos': [{'content': text, 'status': 'pending'}], 'notes': {'index.md': text}}), encoding='utf-8')
        snapshots.append(collected_work(state, folder, identity))
        state = ResearchState(snapshots[-1])
    first, second, third = snapshots
    assert third['current_work']['analysis_id'] == 'third'
    assert third['current_work']['stages'] == first['current_work']['stages']
    assert third['current_work']['stages'] != second['current_work']['stages']
    assert third['materials'] == second['materials']
    assert third['material_order'] == second['material_order']


def test_current_work_keeps_same_note_path_separate_between_actual_stages(tmp_path):
    for stage in ('root', 'investigation', 'writing'):
        folder = tmp_path if stage == 'root' else tmp_path / stage
        folder.mkdir(exist_ok=True)
        (folder / 'workspace.json').write_text(json.dumps({
            'todos': [], 'notes': {'index.md': stage}}), encoding='utf-8')
    result = collected_work(ResearchState(), tmp_path, 'a')
    assert set(result['current_work']['stages']) == {'root', 'investigation', 'writing'}
    for stage, pointers in result['current_work']['stages'].items():
        assert result['materials'][pointers['notes']['index.md']]['text'] == stage


@pytest.mark.parametrize('had_current_work', [False, True])
def test_absent_workspace_does_not_present_historical_notes_as_current(tmp_path, had_current_work):
    state = ResearchState()
    state.add('old-note', {'kind': 'note', 'path': 'index.md', 'text': 'old'})
    previous = state.snapshot()
    if had_current_work:
        previous['current_work'] = {'analysis_id': 'old', 'stages': {'root': {'notes': {'index.md': 'old-note'}}}}
    result = collected_work(ResearchState(previous), tmp_path, 'new')
    assert result['current_work'] == {'analysis_id': 'new', 'stages': {}}
    assert result['materials'] == previous['materials']
    assert ('current_work' in previous) == had_current_work


def test_current_work_uses_canonical_ids_and_drops_pointers_retired_in_a_later_stage(tmp_path):
    state = ResearchState()
    state.add('canonical-note', {'kind': 'note', 'path': 'index.md', 'text': 'original'})
    state.add('canonical-questions', {'kind': 'questions', 'todos': []})
    (tmp_path / 'workspace.json').write_text(json.dumps({
        'todos': [], 'notes': {'index.md': 'original'}}), encoding='utf-8')
    first = collected_work(state, tmp_path, 'first')
    assert first['current_work']['stages']['root'] == {
        'questions': 'canonical-questions', 'notes': {'index.md': 'canonical-note'}}
    writing = tmp_path / 'writing'
    writing.mkdir()
    (writing / 'research_removals.json').write_text(json.dumps([
        {'id': 'canonical-note', 'reason': 'superseded'},
        {'id': 'canonical-questions', 'reason': 'resolved'}]), encoding='utf-8')
    result = collected_work(ResearchState(first), tmp_path, 'second')
    assert result['current_work']['stages'] == {'root': {'notes': {}}}
    assert set(result['removed']) == {'canonical-note', 'canonical-questions'}
    assert result['materials'] == {}
    assert first['materials']['canonical-note']['text'] == 'original'
