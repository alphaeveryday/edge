"""News discovery preserves events and corrections, not a sample of recent articles."""
from copy import deepcopy

import pytest

from edge_analysis_v2.sources.database import DatabaseTools
from edge_analysis_v2.tools.execution import ToolInputError


AT = '2026-10-16T00:00:00+00:00'


def source():
    return {'context': {'etf_code': '091160', 'analysis_at': AT}, 'instruments': [],
            'holdings': [], 'news': [], 'news_events': [],
            'news_scope': {'start_at': '2026-04-19T00:00:00+00:00', 'end_at': AT,
                           'target_ids': ['stock', 'issuer'], 'issuer_ids': ['issuer']}}


def add(data, identity, *, thread=None, novelty='UNKNOWN', status='ACTIVE', when=AT):
    data['news'].append({'news_id': identity, 'title': identity, 'body': 'stored excerpt',
                        'published_at': when, 'available_at': when, 'body_kind': 'excerpt'})
    data['news_events'].append({'event_id': identity, 'event_type': 'CONTRACT', 'event_date': '2026-10-15',
                               'stage': 'SIGNED', 'status': status, 'available_at': when,
                               'thread_id': thread, 'novelty': novelty, 'evaluated_at': when,
                               'unknown_reason': None, 'news_ids': [identity], 'issuer_ids': ['issuer']})


def result(tools, name, **arguments):
    return tools.call(name, arguments)['result']


def test_rebroadcasts_cannot_displace_a_distinct_event_or_hide_a_correction():
    data = source()
    add(data, 'first', thread='t', novelty='FIRST_IN_THREAD')
    for i in range(1001):
        add(data, f'repeat-{i}', thread='t', novelty='DUPLICATE_REBROADCAST')
    add(data, 'correction', thread='t', novelty='CORRECTION')
    add(data, 'independent')
    tools = DatabaseTools(data)
    listing = result(tools, 'list_events')
    assert listing['total_count'] == 2
    assert {row['event_key'] for row in listing['items']} == {'thread:t', 'event:independent'}
    timeline = result(tools, 'get_event_timeline', event_key='thread:t')
    assert {row['event_id'] for row in timeline['items']} >= {'first', 'correction'}
    assert timeline['folded_rebroadcast_count'] == 1001
    assert all('repeated_by' not in row for row in timeline['items'])
    assert result(tools, 'get_issue_evidence', news_ids=['correction'], include_body=True)['news'][0]['body']
    assert result(tools, 'get_issue_evidence', news_ids=['correction'], include_body=False)['news'][0]['news_id'] == 'correction'
    assert 'search_news_threads' not in {s['function']['name'] for s in tools.schemas}


def test_same_company_and_day_never_merge_unknown_events():
    data = source()
    add(data, 'a'); add(data, 'b')
    assert result(DatabaseTools(data), 'list_events')['total_count'] == 2


def test_cursor_binds_snapshot_and_filters_and_visits_every_event_once():
    data = source()
    for i in range(45): add(data, str(i))
    tools = DatabaseTools(data)
    page = result(tools, 'list_events', limit=20)
    ids = [x['event_key'] for x in page['items']]
    cursor = page['next_cursor']
    with pytest.raises(ToolInputError): result(tools, 'list_events', cursor=cursor, event_types=['OTHER'])
    changed = deepcopy(data); add(changed, 'new')
    with pytest.raises(ToolInputError): result(DatabaseTools(changed), 'list_events', cursor=cursor)
    while page['next_cursor']:
        page = result(tools, 'list_events', cursor=page['next_cursor'])
        ids.extend(x['event_key'] for x in page['items'])
    assert len(ids) == len(set(ids)) == 45


def test_changes_distinguish_repeat_correction_relink_rejection_and_missing_baseline():
    before = source(); add(before, 'a', thread='t', novelty='FIRST_IN_THREAD'); add(before, 'b')
    current = deepcopy(before)
    current['context']['analysis_at'] = '2026-10-17T00:00:00+00:00'
    current['news_scope']['end_at'] = current['context']['analysis_at']
    add(current, 'repeat', thread='t', novelty='DUPLICATE_REBROADCAST')
    add(current, 'correction', thread='t', novelty='CORRECTION')
    current['news_events'][0]['thread_id'] = 'other'
    current['news_events'][1]['status'] = 'REJECTED'
    tools = DatabaseTools(current)
    assert result(tools, 'get_event_changes')['baseline_status'] == 'missing'
    tools.set_previous_news_snapshot(DatabaseTools(before).news_snapshot())
    changes = result(tools, 'get_event_changes')
    assert {x['change'] for x in changes['items']} >= {'repeat_only', 'correction', 'link_changed', 'rejected'}


def test_scope_exit_is_not_retraction_and_snapshot_has_no_body():
    before = source(); add(before, 'a')
    old = DatabaseTools(before).news_snapshot()
    assert 'stored excerpt' not in str(old)
    current = source(); current['context']['analysis_at'] = '2026-10-17T00:00:00+00:00'
    current['news_scope'].update(target_ids=['other'], issuer_ids=['other'], end_at=current['context']['analysis_at'])
    tools = DatabaseTools(current); tools.set_previous_news_snapshot(old)
    assert result(tools, 'get_event_changes')['items'][0]['change'] == 'scope_exit_or_missing'
    assert any(row['change'] == 'document_scope_exit_or_missing' for row in result(tools, 'get_event_changes')['items'])
    assert old == DatabaseTools(before).news_snapshot()


def test_unthreaded_search_and_future_data_are_not_silently_included():
    data = source(); add(data, 'linked', thread='t', novelty='FIRST_IN_THREAD')
    add(data, 'future', when='2026-10-17T00:00:00+00:00')
    data['news'].append({'news_id':'loose', 'title':'loose', 'body':None, 'published_at':AT, 'available_at':AT})
    tools = DatabaseTools(data)
    assert result(tools, 'list_events')['total_count'] == 1
    assert [r['news_id'] for r in result(tools, 'search_news_articles', query='', offset=0, unlinked_only=True)['articles']] == ['loose']


def test_new_snapshot_initial_input_replaces_titles_and_legacy_snapshot_stays_absent():
    from test_real_sources import source as complete_source
    legacy = complete_source()
    assert DatabaseTools(legacy).news_snapshot() is None
    data = legacy | {'news_events': [], 'news_scope': source()['news_scope']}
    tools = DatabaseTools(data)
    initial = tools.initial_input()
    assert 'news' not in initial
    assert initial['news_discovery']['recommended_tool'] == 'list_events'
    assert 'target_ids' not in initial['news_discovery']['scope']
    snapshot = tools.news_snapshot()
    snapshot['events'].append({'bad': 'external mutation'})
    assert tools.news_snapshot()['events'] == []


def test_diff_marks_late_acquisition_and_rejects_invalid_comparison_or_cursor():
    before = source()
    old = DatabaseTools(before).news_snapshot()
    current = source()
    current['context']['analysis_at'] = '2026-10-17T00:00:00+00:00'
    current['news'].append({'news_id': 'late', 'title': 'late', 'body': None,
                            'published_at': '2026-10-15T00:00:00+00:00',
                            'available_at': '2026-10-16T12:00:00+00:00'})
    tools = DatabaseTools(current)
    tools.set_previous_news_snapshot(old)
    assert result(tools, 'get_event_changes')['items'][0]['late_acquisition'] is True
    old['documents'].append({'tampered': True})
    with pytest.raises(ValueError, match='integrity'):
        tools.set_previous_news_snapshot(old)
    for bad in ('?', 'e30=', 'W10=', '/w==', 'a'):
        with pytest.raises(ToolInputError):
            result(tools, 'list_events', cursor=bad)


def test_latest_fact_supplies_summary_and_rejected_evidence_remains_searchable():
    data = source()
    add(data, 'a-first', thread='t', novelty='FIRST_IN_THREAD', when='2026-10-14T00:00:00+00:00')
    add(data, 'z-correction', thread='t', novelty='CORRECTION')
    add(data, 'rejected', status='REJECTED')
    tools = DatabaseTools(data)
    listing = result(tools, 'list_events')
    assert listing['items'][0]['title'] == 'z-correction'
    assert listing['items'][0]['news_ids'][0] == 'z-correction'
    assert listing['unlinked_article_count'] == 1
    assert result(tools, 'search_news_articles', query='', offset=0, unlinked_only=True)['articles'][0]['news_id'] == 'rejected'


def test_corrections_precede_repeat_only_changes_and_repeats_are_counted():
    before = source(); add(before, 'first', thread='t', novelty='FIRST_IN_THREAD')
    current = deepcopy(before)
    current['context']['analysis_at'] = '2026-10-17T00:00:00+00:00'
    for index in range(30):
        add(current, f'a-repeat-{index}', thread='t', novelty='DUPLICATE_REBROADCAST')
    add(current, 'z-correction', thread='t', novelty='CORRECTION')
    tools = DatabaseTools(current)
    tools.set_previous_news_snapshot(DatabaseTools(before).news_snapshot())
    page = result(tools, 'get_event_changes', limit=1)
    assert page['items'][0]['change'] == 'correction'
    assert page['change_counts']['repeat_only'] == 30


def test_company_filter_uses_captured_issuer_mapping_and_binds_cursor():
    data = source()
    data['source_instrument_ids'] = {'091160': 'etf', '000001': 'stock', '000002': 'other'}
    data['source_issuer_ids'] = {'000001': 'issuer', '000002': 'other-issuer'}
    for index in range(3): add(data, str(index))
    add(data, 'other')
    data['news_events'][-1]['issuer_ids'] = ['other-issuer']
    tools = DatabaseTools(data)
    page = result(tools, 'list_events', instrument_ids=['000001'], limit=1)
    assert page['total_count'] == 3
    assert page['items'][0]['related_instrument_ids'] == ['000001']
    with pytest.raises(ToolInputError):
        result(tools, 'list_events', instrument_ids=['000002'], cursor=page['next_cursor'])
    with pytest.raises(ToolInputError, match='Allowed'):
        result(tools, 'list_events', instrument_ids=['BAD'])
    with pytest.raises(ToolInputError, match='unavailable'):
        result(DatabaseTools(source()), 'list_events', instrument_ids=['000001'])


def test_rebroadcast_only_thread_keeps_one_representative_without_inventing_original():
    data = source()
    add(data, 'older', thread='t', novelty='DUPLICATE_REBROADCAST', when='2026-10-15T00:00:00+00:00')
    add(data, 'latest', thread='t', novelty='DUPLICATE_REBROADCAST')
    page = result(DatabaseTools(data), 'get_event_timeline', event_key='thread:t')
    assert len(page['items']) == 1
    assert page['items'][0]['event_id'] == 'latest'
    assert page['items'][0]['representative_rebroadcast'] is True
    assert page['original_unavailable'] is True
    assert page['folded_rebroadcast_count'] == 1
