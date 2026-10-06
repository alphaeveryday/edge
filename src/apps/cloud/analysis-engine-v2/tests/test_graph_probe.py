"""A probe run must leave the answer, the stored calls and one verdict per hypothesis."""
import asyncio
import json

from edge_analysis_v2.quality.graph_probe import run_probe

CATALOG = {'modelChanged': False, 'relations': [], 'objects': []}


def test_a_probe_records_its_calls_and_judges_only_its_hypotheses(tmp_path):
    async def model(*, call, schemas, artifacts, **_):
        assert [s['function']['name'] for s in schemas] == ['get_result_page', 'get_ontology_schema', 'search_objects', 'get_linked_objects', 'get_etf_holdings', 'summarize_etf_holdings', 'compare_holdings_dates', 'resolve_securities']
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
