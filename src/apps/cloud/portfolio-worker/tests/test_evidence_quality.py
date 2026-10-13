from edge_portfolio.evidence_quality import inspect_body, source_page


def test_navigation_only_source_does_not_count_as_research():
    body = '# Official ETF\nURL: https://issuer.example/etf\n' + '\n'.join(['Home', 'Products', 'Contact us', '[Funds](https://issuer.example/funds)'] * 10)
    assert len(body) > 399
    assert inspect_body(body, 24000)['status'] == 'unavailable'


def test_collected_time_never_replaces_publication_or_claim_date():
    body = '# ETF factsheet\nPublished: 2023-01-01\n' + 'The fund invests in a diversified portfolio and its expense ratio is reported in the annual disclosure.'
    result = inspect_body(body, 24000)
    assert result['published_at'] == '2023-01-01'
    assert result['claim_as_of'] is None
    assert result['coverage_status'] == 'requires_field_review'


def test_short_factsheet_table_rows_are_readable_research():
    body = '# Fund holdings\nAs of September 30, 2026\n| Holding | Weight |\n| Apple | 10.2% |\n| Microsoft | 9.5% |\nExpense ratio: 0.03%'
    assert inspect_body(body, 24000)['status'] == 'available'


def test_source_pagination_retains_stable_passage_ids_and_tail_facts():
    source = {'id': 's1', 'excerpt': 'original', 'passages': [{'id': f'p{i}', 'text': f'fact {i}'} for i in range(50)]}
    first = source_page(source)
    second = source_page(source, first['next_offset'])
    third = source_page(source, second['next_offset'])
    assert third['passages'][-1]['id'] == 'p49'
    assert third['next_offset'] is None
    assert 'excerpt' not in first
    assert inspect_body('A substantive sentence describing the ETF and expense data.' * 1000, 24000)['truncated']
