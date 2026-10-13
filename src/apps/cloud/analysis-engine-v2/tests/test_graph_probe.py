"""A probe run must leave the answer, the stored calls and one verdict per hypothesis."""
import asyncio
import json

from edge_analysis_v2.quality.graph_probe import run_probe

CATALOG = {'modelChanged': False, 'relations': [], 'objects': []}


def test_a_probe_records_its_calls_and_judges_only_its_hypotheses(tmp_path):
    async def model(*, call, schemas, artifacts, **_):
        assert [s['function']['name'] for s in schemas] == ['get_result_page', 'get_ontology_schema', 'search_objects', 'get_linked_objects', 'get_etf_holdings', 'sum_holding_weights', 'compare_holdings_between_dates', 'find_securities_by_ticker', 'get_objects', 'list_events', 'find_similar_events', 'get_event_timeline', 'read_article', 'read_document_excerpts',
            'get_price_observations', 'get_flow_observations', 'calculate_returns', 'count_rising_and_falling', 'calculate_nav_premium',
            'list_macro_series', 'get_financial_observations', 'get_macro_observations', 'compare_two_figures',
            'estimate_contract_profit', 'compare_actual_to_expectation']
        artifacts.mkdir(parents=True)
        (artifacts/'events.jsonl').write_text(json.dumps({'message_type': 'ResultMessage',
            'message': {'usage': {'input_tokens': 7, 'output_tokens': 2}}}) + '\n', encoding='utf8')
        return {'answer': '확인하지 못했습니다', 'limitations': [], 'claims': []}
    hypotheses = [{'id': 'none', 'kind': '비호출', 'claim': 'no paging without a dataset', 'probe': 'P',
                   'check': {'count': {'tool': 'get_result_page', 'max': 0}}}]
    report = asyncio.run(run_probe('P', {'question': 'q', 'cutoff': '2026-10-05T00:00:00+00:00'}, hypotheses,
        run=lambda statement, parameters: [], catalog=CATALOG, digest='d', runs_dir=tmp_path, key='secret', model='m', model_call=model))
    saved = json.loads(next(tmp_path.glob('P-*/benchmark.json')).read_text(encoding='utf8'))
    assert saved['status'] == 'answered' and saved['usage'] == {'input_tokens': 7, 'output_tokens': 2}
    assert [(v['id'], v['status']) for v in saved['intent']] == [('none', 'pass')]
    assert report['path'].startswith(str(tmp_path))


def test_a_failed_model_run_is_stored_as_an_error_without_the_key(tmp_path):
    async def model(**_):
        raise ValueError('rejected key secret')
    asyncio.run(run_probe('P', {'question': 'q', 'cutoff': '2026-10-05T00:00:00+00:00'}, [], run=lambda s, p: [],
        catalog=CATALOG, digest='d', runs_dir=tmp_path, key='secret', model='m', model_call=model))
    saved = next(tmp_path.glob('P-*/benchmark.json')).read_text(encoding='utf8')
    assert '"status": "error"' in saved and 'secret' not in saved


def test_repeats_started_in_the_same_second_get_separate_folders(tmp_path):
    async def model(**_):
        raise ValueError('fails at once')
    for _ in range(3):
        asyncio.run(run_probe('P', {'question': 'q', 'cutoff': '2026-10-05T00:00:00+00:00'}, [], run=lambda s, p: [],
            catalog=CATALOG, digest='d', runs_dir=tmp_path, key='secret', model='m', model_call=model))
    assert len(list(tmp_path.glob('P-*/benchmark.json'))) == 3
