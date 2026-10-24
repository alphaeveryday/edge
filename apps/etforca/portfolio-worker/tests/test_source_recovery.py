from types import SimpleNamespace

import pytest

from edge_portfolio.agent import ExaEvidence


@pytest.mark.parametrize('suffix', ['', '.', ',', ';'])
async def test_a_product_page_can_lead_directly_to_its_official_factsheet(suffix):
    calls = []
    class Session:
        async def call_tool(self, name, args):
            calls.append(args)
            body = 'The ETF holds a diversified basket of equities and reports its expense ratio.\n[Official factsheet](https://issuer.example/factsheet.pdf)' + suffix
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text=body)])
    evidence = ExaEvidence(Session())
    evidence.discovered.add('https://issuer.example/fund')
    page = await evidence.call('fetch_source', {'url': 'https://issuer.example/fund'})
    assert 'https://issuer.example/factsheet.pdf' in page['fetchable_urls']
    await evidence.call('fetch_source', {'url': 'https://issuer.example/factsheet.pdf'})
    assert len(calls) == 2


async def test_duplicate_search_reuses_results_and_budget_for_a_different_path():
    calls = []
    class Session:
        async def call_tool(self, name, args):
            calls.append(args)
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='URL: https://issuer.example/fund')])
    evidence = ExaEvidence(Session())
    query = {'query': 'ETF holdings', 'objective': 'Check holdings'}
    await evidence.call('search_sources', query)
    await evidence.call('search_sources', query)
    await evidence.call('search_sources', {**query, 'query': 'ETF annual report holdings'})
    assert evidence.calls == len(calls) == 2


@pytest.mark.parametrize('document_url', ['https://issuer.example/factsheet.pdf', 'https://issuer.example/download?id=123',
                                          'https://issuer.example/download?file=report.pdf'])
async def test_empty_navigation_is_recorded_and_its_pdf_link_is_still_discovered(document_url):
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text=f'Home\n[Factsheet]({document_url})')])
    evidence = ExaEvidence(Session())
    evidence.discovered.add('https://issuer.example/fund')
    with pytest.raises(ValueError, match='INSUFFICIENT_SOURCE_BODY'):
        await evidence.call('fetch_source', {'url': 'https://issuer.example/fund'})
    with pytest.raises(ValueError, match='SOURCE_PATH_EXHAUSTED'):
        await evidence.call('fetch_source', {'url': 'https://issuer.example/fund'})
    assert evidence.calls == 1
    assert document_url in evidence.discovered
    assert document_url in evidence.search_targets
    assert evidence.attempts[-1]['reason'] == 'INSUFFICIENT_SOURCE_BODY'
    assert evidence.sources == []


async def test_transient_failures_allow_one_retry_and_leave_other_paths_available():
    class Session:
        async def call_tool(self, *args): raise TimeoutError('private provider details')
    evidence = ExaEvidence(Session())
    query = {'query': 'ETF holdings', 'objective': 'Check holdings'}
    for _ in range(2):
        with pytest.raises(ValueError, match='^EXA_FAILED$'):
            await evidence.call('search_sources', query)
    with pytest.raises(ValueError, match='SOURCE_PATH_EXHAUSTED'):
        await evidence.call('search_sources', query)
    with pytest.raises(ValueError, match='^EXA_FAILED$'):
        await evidence.call('search_sources', {**query, 'query': 'ETF official factsheet'})
    assert evidence.calls == 3
    assert len(evidence.attempts) == 3
    assert all(attempt['reason'] == 'EXA_FAILED' for attempt in evidence.attempts)


async def test_parentheses_in_explicit_document_urls_remain_fetchable():
    url = 'https://issuer.example/factsheet(2026).pdf'
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='URL: ' + url)])
    evidence = ExaEvidence(Session())
    result = await evidence.call('search_sources', {'query': 'ETF', 'objective': 'factsheet'})
    assert result['fetchable_urls'] == [url]


async def test_expansion_recovery_makes_cached_passages_readable_after_initial_failure():
    calls = []
    class Session:
        async def call_tool(self, name, args):
            calls.append(args)
            text = 'Home' if args['maxCharacters'] == 24000 else 'The ETF holds a diversified basket of equities and reports its expense ratio.'
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text=text)])
    evidence = ExaEvidence(Session())
    url = 'https://issuer.example/fund'
    evidence.discovered.add(url)
    with pytest.raises(ValueError): await evidence.call('fetch_source', {'url': url})
    expanded = await evidence.call('fetch_source', {'url': url, 'expand': True})
    cached = await evidence.call('fetch_source', {'url': url})
    assert cached['passages'] == expanded['passages']
    assert len(calls) == 2


async def test_optional_defaults_cannot_restart_an_exhausted_fetch_path():
    class Session:
        async def call_tool(self, *args): raise TimeoutError()
    evidence = ExaEvidence(Session())
    url = 'https://issuer.example/fund'
    evidence.discovered.add(url)
    for options, reason in [({}, 'EXA_FAILED'), ({'offset': 0}, 'EXA_FAILED'),
                           ({'expand': False}, 'SOURCE_PATH_EXHAUSTED'),
                           ({'offset': 0, 'expand': False}, 'SOURCE_PATH_EXHAUSTED')]:
        with pytest.raises(ValueError, match=reason):
            await evidence.call('fetch_source', {'url': url, **options})
    assert evidence.calls == 2


async def test_cached_followup_page_restores_its_document_links():
    class Session:
        async def call_tool(self, *args):
            return SimpleNamespace(isError=False, content=[SimpleNamespace(type='text', text='The ETF holds a diversified basket of equities and reports its expense ratio.')])
    evidence = ExaEvidence(Session())
    url = 'https://issuer.example/fund'
    pdf = 'https://issuer.example/factsheet.pdf'
    evidence.sources = [{'id': 's1', 'url': url, 'fetchable_urls': [pdf],
                         'passages': [{'id': 'p1', 'text': 'Official fund documentation.'}]}]
    evidence.discovered.add(url)
    await evidence.call('fetch_source', {'url': url})
    result = await evidence.call('fetch_source', {'url': pdf})
    assert result['url'] == pdf
    assert evidence.calls == 1
