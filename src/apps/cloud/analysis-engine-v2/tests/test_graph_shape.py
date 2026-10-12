"""A large result states what its page leaves out, and a won amount is written out once by the tool."""
from types import SimpleNamespace

from edge_analysis_v2.tools.graph.provider import GraphTools
from edge_analysis_v2.tools.graph.shape import won_text


def event(i, kind, **more):
    """A stored event as the graph returns it: its fields are properties under the catalog's names."""
    return {'object_type': 'SourceEvent', 'object_id': f'e{i}', 'title': f'event {i}', 'properties': {'eventType': kind, **more}}


def test_a_result_larger_than_its_page_says_so_first_and_profiles_every_row():
    # WHY: nine benchmark runs concluded from the first 20 of several hundred rows.
    tools = object.__new__(GraphTools)
    tools.store = SimpleNamespace(cutoff='2026-10-05T14:59:59+00:00')
    events = [event(i, 'CONTRACT.SIGNED' if i % 4 else 'ORDER.PREFERRED_BIDDER', eventDate=f'2026-0{8 + i % 2}-1{i % 9}',
                    lifecycleStage='ONGOING') for i in range(45)]
    value, dataset = tools.result(events, limit=20)
    assert list(value)[:4] == ['total_rows', 'page', 'notice', 'overview']
    assert '20 of 45 rows' in value['notice'] and len(value['items']) == 20 and len(dataset['items']) == 45
    shape = {key: {r['value']: r['rows'] for r in rows} for key, rows in value['overview'].items()}
    # WHY: the shape looked for event_type and event_date, names no stored object has, and a search of 135 events came back with no shape.
    assert shape['eventType'] == {'CONTRACT.SIGNED': 33, 'ORDER.PREFERRED_BIDDER': 12}
    assert sum(shape['eventDate_by_month'].values()) == 45
    assert 'lifecycleStage' not in shape   # one value for every row tells nothing
    small, _ = tools.result(events[:5], limit=20)
    assert small['page']['complete'] and 'notice' not in small and 'overview' not in small


def test_a_search_too_broad_to_read_returns_its_shape_and_no_rows():
    # WHY: telling the agent what a page left out did not make it narrow the search; withholding the rows did.
    tools = object.__new__(GraphTools)
    tools.store = SimpleNamespace(cutoff='2026-10-05T14:59:59+00:00')
    broad = [event(i, 'A' if i % 3 else 'B') for i in range(135)]
    value, dataset = tools.result(broad, limit=20, search=True)
    assert value['items'] == [] and '135 rows match' in value['notice'] and 'eventType' in value['overview']
    assert len(dataset['items']) == 135 and value['page'] == {'complete': False, 'next_offset': 0}
    assert len(tools.result(broad, limit=20)[0]['items']) == 20                      # a set asked for by identity keeps its first page
    assert len(tools.result(broad[:45], limit=20, search=True)[0]['items']) == 45    # 45 matches can be read, so all are shown


def test_observations_come_back_as_a_table_that_fits_one_response():
    # WHY: 33 results in 26 runs were larger than a response can be, and the agent received an error instead
    # of any row. A price row was 580 characters of which the title and the id were repeated.
    from edge_analysis_v2.tools.graph.shape import BUDGET, table
    tools = object.__new__(GraphTools)
    tools.store = SimpleNamespace(cutoff='2026-10-05T14:59:59+00:00')
    bars = [{'object_type': 'DailyBar', 'object_id': f'["inst","2026-09-{d:02d}"]', 'title': f'한화오션 / Daily prices / 2026-09-{d:02d}',
             'properties': {'id': 'again', 'displayTitle': 'again', 'securityName': '한화오션', 'tradeDate': f'2026-09-{d:02d}', 'closePrice': str(1000 + d)}}
            for d in range(1, 31)]
    value, dataset = tools.result(bars, limit=100, table=True)
    assert value['columns'] == ['tradeDate', 'closePrice'] and value['rows'][0] == ['2026-09-01', '1001']
    assert value['same_for_all_rows'] == {'object_type': 'DailyBar', 'securityName': '한화오션'} and 'items' not in value
    assert len(dataset['items']) == 30 and dataset['items'][0]['properties']['id'] == 'again'   # calculations read the stored objects
    assert table(bars[:1], keep_id=True)[0] == ['object_type', 'securityName', 'tradeDate', 'closePrice', 'object_id']
    assert len(tools.result(bars, limit=5, table=True)[0]['rows']) == 30   # short rows that were asked for are all shown
    assert len(tools.result(bars * 7, limit=5, table=True)[0]['rows']) == 210   # bounded by size, not by a row count
    wide = [{'object_id': f'e{i}', 'title': 'A / x', 'text': '가' * 2000} for i in range(60)]
    shown, _ = tools.result(wide, limit=100, search=True)
    import json
    assert 0 < len(shown['items']) < 60 and len(json.dumps(shown, ensure_ascii=False)) < BUDGET + 4000
    assert shown['page'] == {'complete': False, 'next_offset': len(shown['items'])} and 'as many as one response holds' in shown['notice']


def table_tools():
    tools = object.__new__(GraphTools)
    tools.store = SimpleNamespace(cutoff='2026-10-05T14:59:59+00:00')
    return tools


def test_a_table_whose_rows_differ_in_nothing_is_still_a_table_with_what_they_share():
    # WHY: two links to the same object came back as items [[], []] and the values every row shared were dropped.
    rows = [{'object_type': 'Equity', 'object_id': 'a', 'title': 'A', 'properties': {'ticker': '005930', 'marketCode': 'XKRX'}}] * 2
    value, dataset = table_tools().result(rows, table='plain')
    assert value['columns'] == [] and value['rows'] == [[], []] and 'items' not in value
    assert value['same_for_all_rows'] == {'object_type': 'Equity', 'ticker': '005930', 'marketCode': 'XKRX'}
    assert dataset['display_same'] == value['same_for_all_rows']


def test_a_later_page_of_a_table_repeats_what_every_row_shares(tmp_path):
    # WHY: a page read through the stored result had rows without the security type and weight the first page said once.
    from edge_analysis_v2.tools.graph.store import EvidenceStore
    rows = [{'object_type': 'Equity', 'object_id': f'e{i}', 'title': f'T{i}', 'properties': {'ticker': f'{i:06d}', 'marketCode': 'XKRX'}}
            for i in range(30)]
    value, dataset = table_tools().result(rows, table='plain')
    store = object.__new__(EvidenceStore)
    store.reference = lambda ref, kind: dataset
    page = store.page({'tool_run_id': 'r'}, 20, 5)
    assert page['columns'] == value['columns'] == ['ticker'] and len(page['rows']) == 5
    assert page['same_for_all_rows'] == {'object_type': 'Equity', 'marketCode': 'XKRX'}


def test_a_later_page_counts_what_every_row_shares_toward_its_size():
    # WHY: the first page held 19 rows beside a long shared value and the next page 40, over the size a response can have.
    import json
    from edge_analysis_v2.tools.graph.shape import BUDGET
    from edge_analysis_v2.tools.graph.store import EvidenceStore
    rows = [{'object_type': 'Document', 'object_id': f'd{i}', 'title': f'T{i}', 'properties': {'body': 'x' * 22000, 'part': 'y' * 1000 + str(i)}}
            for i in range(60)]
    value, dataset = table_tools().result(rows, table='plain')
    store = object.__new__(EvidenceStore)
    store.reference = lambda ref, kind: dataset
    page = store.page({'tool_run_id': 'r'}, len(value['rows']), 100)
    assert len(json.dumps(page, ensure_ascii=False)) < BUDGET + 2000 and len(page['rows']) <= len(value['rows']) + 1


def test_the_shape_of_linked_rows_counts_the_objects_they_lead_to():
    # WHY: a cut list of linked objects came with an empty overview, because their names sit under target.
    from edge_analysis_v2.tools.graph.shape import overview
    rows = [{'source': {'object_id': 's'}, 'target': {'object_type': 'Equity' if i % 2 else 'ETF', 'object_id': f't{i}',
                                                   'properties': {'ticker': f'{i:06d}'}}} for i in range(4)]
    counts = {key: {r['value']: r['rows'] for r in found} for key, found in overview(rows).items()}
    assert counts['object_type'] == {'Equity': 2, 'ETF': 2} and len(counts['ticker']) == 4


def test_what_every_row_shares_counts_toward_the_size_of_a_response():
    # WHY: a long value shared by every row moved out of the rows, so the size limit no longer saw it.
    from edge_analysis_v2.tools.graph.shape import BUDGET, fit
    rows = [['x' * 1000]] * 60
    assert fit(rows, 60) > fit(rows, 60, used=BUDGET // 2) > 1
    assert fit(rows, 60, used=BUDGET * 2) == 1   # one row is always shown


def test_the_shape_of_a_cut_result_counts_toward_the_size_of_its_response():
    # WHY: 50 rows with long distinct names were cut to 13 rows, and the shape listing those names made the response twice the limit.
    import json
    from edge_analysis_v2.tools.graph.shape import BUDGET
    rows = [{'object_type': 'Company', 'object_id': f'c{i}', 'title': f'c{i}', 'properties': {'name': f'{i:04d}' + '가' * 3000, 'countryCode': 'KR' if i % 2 else 'US'}}
            for i in range(50)]
    value, _ = table_tools().result(rows, limit=50)
    assert 0 < len(value['items']) < 50 and len(json.dumps(value, ensure_ascii=False)) < BUDGET + 4000
    assert 'name' not in value['overview']   # a text that long is not a category to narrow by


def test_a_won_amount_is_written_out_exactly_so_no_digit_is_left_to_count():
    # WHY: answers wrote 1,447,000,000원 as 145억 and 2,004,376,374,000원 as 2조 437억.
    assert won_text(1447000000) == '14억 4,700만원'
    assert won_text('2004376374000.00000000') == '2조 43억 7,637만 4,000원'
    assert won_text(-2069000000) == '-20억 6,900만원'
    assert won_text(1000000000000) == '1조원'
    assert won_text(12345678901234567) == '12,345조 6,789억 123만 4,567원'
    assert won_text(0) == '0원' and won_text(None) is None


def test_market_days_become_a_half_open_korean_range(tmp_path):
    tools = GraphTools(lambda s, p: [], {'modelChanged': False, 'relations': [], 'objects': []}, tmp_path, '2026-10-04T16:00:00+00:00')
    assert tools.time_bounds('2026-10-05', '2026-10-05') == {'start_at': '2026-10-05T00:00:00+09:00', 'end_at': '2026-10-06T00:00:00+09:00'}
