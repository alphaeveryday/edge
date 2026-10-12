"""Small initial context must preserve access to every original observation."""
from edge_analysis_v2.agent.work_context import WorkContext


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
