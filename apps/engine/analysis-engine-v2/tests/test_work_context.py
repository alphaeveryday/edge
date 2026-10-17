"""Small initial context must preserve access to every original observation."""
from edge_analysis_v2.agent.work_context import WorkContext


def test_catalog_order_is_stable_without_repeating_originals_after_database_roundtrip():
    import json
    from edge_analysis_v2.analysis.research_state import ResearchState
    state = ResearchState()
    state.add('z', {'text': 'original\n  spacing', 'nested': {'z': 1, 'a': [2, 1]}})
    state.add('m', {'text': 'second'})
    first = {'context': {'analysis_at': '2026-10-16'}, 'research_state': state.snapshot()}
    restored = ResearchState(json.loads(json.dumps(state.snapshot(), sort_keys=True)))
    restored.add('a', {'text': 'new'})
    second = {'context': {'analysis_at': '2026-10-17'}, 'research_state': restored.snapshot()}
    projected = [WorkContext(value).initial for value in (first, second)]
    for raw, value in zip((first, second), projected):
        assert WorkContext(raw).sources['research_state'] == raw['research_state']
        assert next(iter(value)) == 'research_state'
        assert next(iter(value['research_state'])) == 'catalog'
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    prefix = encode(projected[0]['research_state']['catalog'])[:-1]
    assert encode(projected[1]['research_state']['catalog']).startswith(prefix)
    assert first['research_state']['materials']['z']['nested'] == {'z': 1, 'a': [2, 1]}


def test_revision_body_stays_in_first_input_while_originals_remain_retrievable():
    initial = {'previous_analysis': {'detail': {'original': 'unchanged'}},
               'previous_items': [{'item_id': 'kept'}],
               'research_state': {'version': 1, 'materials': {'a': {'text': 'original'}}}}
    context = WorkContext(initial)
    for key in ('previous_analysis', 'previous_items'):
        assert context.initial[key] == initial[key]
    assert 'materials' not in context.initial['research_state']
    assert context.read('research_state', 'a')['rows'] == [{'text': 'original'}]
    assert context.consulted == []
    context.initial['previous_analysis']['detail']['original'] = 'changed'
    assert initial['previous_analysis']['detail']['original'] == 'unchanged'


def test_only_current_unresolved_questions_are_inlined_and_stale_pointers_are_not_current():
    raw = {'analysis': {'analysis_id': 'parent'}, 'materials': {
        'q': {'kind': 'questions', 'todos': [
            {'content': 'unconfirmed contract', 'status': 'pending'},
            {'content': 'await next filing', 'status': 'completed', 'closure': 'structural_limit'},
            {'content': 'immaterial question', 'status': 'completed', 'closure': 'no_effect'},
            {'content': 'old solved question', 'status': 'completed', 'answer': 'large old answer'}]},
        'n': {'kind': 'note', 'path': 'answer.md', 'text': 'large old report'}},
        'current_work': {'analysis_id': 'parent', 'stages': {'root': {'questions': 'q', 'notes': {'answer.md': 'n'}}}}}
    current = WorkContext({'research_state': raw}).initial['research_state']['current_work']
    assert current['status'] == 'recorded'
    assert current['stages']['root']['open_questions'] == [
        {'content': 'unconfirmed contract', 'status': 'pending'},
        {'content': 'await next filing', 'status': 'completed', 'closure': 'structural_limit'}]
    raw['current_work']['analysis_id'] = 'older-parent'
    stale = WorkContext({'research_state': raw}).initial['research_state']
    assert stale['current_work']['status'] == 'unavailable'
    assert set(stale['catalog']) == {'q', 'n'}


def test_alias_reads_original_and_retired_material_is_not_reintroduced():
    import pytest
    from edge_analysis_v2.analysis.research_state import ResearchState
    state = ResearchState()
    state.add('a', {'text': 'kept'})
    state.add('alias', {'text': 'kept'})
    context = WorkContext({'research_state': state.snapshot()})
    assert context.read('research_state', 'alias')['rows'] == [{'text': 'kept'}]
    state.remove([{'id': 'a', 'reason': 'superseded'}])
    context = WorkContext({'research_state': state.snapshot()})
    assert context.initial['research_state']['catalog'] == {}
    with pytest.raises(ValueError):
        context.read('research_state', 'alias')


def test_research_records_only_pages_actually_read_not_the_unread_database():
    context = WorkContext({'news': list(range(200))})
    page = context.read('news', offset=50, limit=10)
    context.read('news', offset=50, limit=10)
    assert context.consulted == [page]
    assert page['rows'] == list(range(50, 60))


def test_reading_previous_analysis_does_not_reclassify_authored_text_as_source_material():
    previous = {'previous_analysis': {'summary_card': {'summary': 'Prior judgment'}},
                'previous_issue_detail': {'headline': 'Prior issue', 'items': []},
                'previous_items': [{'sentence': 'Prior explanation'}],
                'research_state': {'version': 1, 'materials': {}}}
    context = WorkContext(previous | {'news': [{'title': 'New source'}]})
    for source in previous:
        assert context.read(source)['rows']
    assert context.consulted == []
    news = context.read('news')
    assert context.consulted == [news]


def test_large_tables_are_retrievable_without_being_in_first_prompt():
    raw = {'context': {'analysis_at': '2026-10-05'},
           'prices': {'A': {'columns': ['date', 'close'], 'rows': [[str(i), i] for i in range(500)]}},
           'source_gaps': {'financials': ['not in database']}}
    state = WorkContext(raw)
    assert 'prices' not in state.initial
    assert state.initial['available_sources']['prices']
    first = state.read('prices', 'A', 0, 100)
    second = state.read('prices', 'A', 100, 100)
    assert first['rows'] == raw['prices']['A']['rows'][:100]
    assert second['rows'][0] == ['100', 100]
    assert first['next_offset'] == 100
    assert first['columns'] == ['date', 'close']
    assert state.read('source_gaps', 'financials', 0, 100)['rows'] == ['not in database']


def test_invalid_selector_cannot_read_worker_files():
    import pytest
    state = WorkContext({'news': []})
    with pytest.raises(ValueError):
        state.read('../../secret', '', 0, 10)
    with pytest.raises(ValueError):
        state.read('news', '', -1, 10)


def test_list_sources_need_no_invented_subject_and_bad_selectors_explain_recovery():
    import pytest
    state = WorkContext({'news':[{'title':'Original headline'}], 'context':{'instrument':'NOVA'}})
    assert state.read('news')['rows'] == [{'title':'Original headline'}]
    with pytest.raises(ValueError, match='omit subject'):
        state.read('news', 'NOVA')
    with pytest.raises(ValueError, match='listed key'):
        state.read('context', 'NOVA')


def test_limitation_notices_stay_in_the_first_message_when_raw_rows_move_behind_read_source():
    from edge_analysis_v2.agent.work_context import WorkContext
    initial = {'context': {'etf_code': 'X'}, 'prices': {'X': {'columns': ['date'], 'rows': [['2026-10-01']]}},
               'source_gaps': {'prices': 'high and low not collected'},
               'history_preview': {'truncated': ['prices'], 'note': 'preview rows are not the full history'}}
    first = WorkContext(initial).initial
    assert first['source_gaps'] == initial['source_gaps'] and first['history_preview'] == initial['history_preview']
    assert first['available_sources']['prices'] == {'keys': ['X']} and 'prices' not in first


def test_revision_start_and_missing_baseline_are_visible_without_reading_all_previous_text():
    initial = {'news_discovery': {'snapshot_id': 'current', 'recommended_tool': 'list_events'},
               'news_research': {'mode': 'revision', 'baseline_status': 'missing',
                                 'previous_analysis_id': 'prior', 'recommended_tool': 'get_event_changes'},
               'previous_analysis': {'detail': {'items': [{'id': 'topic', 'sentences': ['long text']}]}}}
    state = WorkContext(initial)
    assert state.initial['news_research'] == initial['news_research']
    assert state.initial['news_discovery'] == initial['news_discovery']
    assert state.initial['previous_analysis'] == initial['previous_analysis']
    assert state.read('previous_analysis')['rows']
