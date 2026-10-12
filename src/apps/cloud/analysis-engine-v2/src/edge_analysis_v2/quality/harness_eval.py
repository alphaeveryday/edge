"""Paid, isolated model evaluation against fixed synthetic research sources; no publication DB."""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path

from edge_analysis_v2.agent.runner import run_model
from edge_analysis_v2.dashboard.jobs import read_settings


def scenario(case):
    calls = []
    def call(name, args):
        identity = f'run-{len(calls) + 1}'
        if name == 'search_news_articles':
            value = {'articles': [], 'scope': 'local database only'}
        elif name == 'search_web':
            value = {'results': [
                {'url': 'https://example.com/filing.pdf', 'title': 'Nova quarterly filing'},
                {'url': 'https://example.com/ir', 'title': 'Nova dated investor presentation'}]}
        elif name == 'read_web_document':
            if args['url'] not in ('https://example.com/filing.pdf', 'https://example.com/ir'):
                value = {'status': 'unavailable', 'reason': 'This URL is not in the fixed source set. Use the URLs returned by search.'}
            elif args['url'].endswith('.pdf'):
                value = {'status': 'unavailable', 'reason': 'Extraction failed. A separate IR page is listed.'}
            elif case == 'available':
                value = {'published_at': '2026-10-01', 'source': 'synthetic official investor presentation',
                         'text': 'Orders rose 40%, but most new shipments begin next year. Current-quarter revenue is projected at 100 versus 100 last year. Current operating margin is 10% versus 20% last year due to customer qualification costs. Full financial guidance has been publicly released.'}
            else:
                value = {'published_at': '2026-10-01', 'source': 'synthetic official investor presentation',
                         'text': 'Orders rose 40%. Nova is private and publishes neither operating profit nor margins. Customer contracts prohibit disclosure of delivery schedules and contract pricing. No public quarterly guidance is issued. Order quantity alone does not establish next-quarter profit.'}
        else:
            raise ValueError('Unknown evaluation tool')
        calls.append({'tool_run_id':identity, 'name':name, 'args':args, 'result':value})
        return {'tool_run_id':identity, 'result':value}
    schemas = []
    for name, fields, description in [
        ('search_news_articles', {'query':{'type':'string'}}, 'Search the local database of news; empty means absent from this database only.'),
        ('search_web', {'query':{'type':'string'}}, 'Search public sources outside the local database.'),
        ('read_web_document', {'url':{'type':'string'}}, 'Read a public source returned by search. A failed page can have alternatives.')]:
        schemas.append({'type':'function', 'function':{'name':name, 'description':description,
            'parameters':{'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}}})
    return call, schemas, calls


async def evaluate(folder, case, key, model):
    call, schemas, calls = scenario(case)
    schema = {'type':'object', 'properties': {'profit_direction':{'enum':['increase','decrease','unknown']},
        'explanation':{'type':'string'}, 'evidence_ids':{'type':'array','items':{'type':'string'}}},
        'required':['profit_direction','explanation','evidence_ids'], 'additionalProperties':False}
    initial = {'context':{'analysis_at':'2026-10-05T08:00:00+00:00', 'instrument':'SYNTHETIC-NOVA'},
        'news':[{'title':'Nova orders rose 40%; an earlier commentary expects higher next-quarter profit.'}],
        'source_gaps':{'financials':['Not loaded in the local database']}, 'web_research':{'enabled':True}}
    result = await run_model(initial=initial,
        prompt='고정 합성 자료의 조사 평가다. 실제 ETF 보고서를 작성하지 않는다. Nova의 수주 증가가 이번 분기 영업이익 증가를 뜻하는지 현재 가능한 최선의 결론을 내라. 성장, 납품 시차, 비용 변화의 설명을 구별하고 필요한 공백을 조사하라. 초기 기사와 DB 범위 밖에 필요한 정보가 있을 수 있다. 이 평가에서는 제공된 도구 응답 ID를 근거로 쓰고 제공된 작은 출력 스키마만 따른다. 근거 없이 숫자를 계산하지 않는다.',
        schemas=schemas, call=call, output_schema=schema, artifacts=folder, key=key, model=model)
    expected = 'decrease' if case == 'available' else 'unknown'
    read_ir = any(c['name']=='read_web_document' and c['args']['url'].endswith('/ir') for c in calls)
    named = {c['tool_run_id']: c for c in calls}
    evidence_valid = bool(result['evidence_ids']) and all(i in named for i in result['evidence_ids'])
    cites_ir = any(i in named and named[i]['name']=='read_web_document'
        and named[i]['args']['url'].endswith('/ir') for i in result['evidence_ids'])
    state = json.loads((folder/'workspace.json').read_text(encoding='utf-8'))
    tool_names = []
    for line in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines():
        event = json.loads(line)
        if event['message_type'] == 'AssistantMessage':
            tool_names.extend(block['name'] for block in event['message'].get('content', []) if 'name' in block)
    registration = next((i for i, name in enumerate(tool_names) if name == 'mcp__workspace__update_tasks'), None)
    research = next((i for i, name in enumerate(tool_names) if name.startswith('mcp__analysis__')), None)
    report = {'case':case, 'model':model, 'result':result, 'checks':{
        'public_alternative_examined': read_ir, 'conclusion_matches_fixed_facts':result['profit_direction']==expected,
        'evidence_ids_exist':evidence_valid, 'primary_source_cited':cites_ir,
        'questions_registered_before_research': registration is not None and research is not None and registration < research,
        'registered_questions_completed': bool(state['todos']) and all(t['status']=='completed' for t in state['todos'])},
        'tool_counts':dict(Counter(c['name'] for c in calls))}
    (folder/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'evaluation_calls.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
    failed = [name for name, passed in report['checks'].items() if not passed]
    if failed:
        raise ValueError('Harness evaluation failed: ' + ', '.join(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--model', default='deepseek-flash')
    parser.add_argument('--case', choices=['available','unavailable'], default='available')
    args = parser.parse_args()
    asyncio.run(evaluate(args.artifacts, args.case, read_settings(args.env_file)['key'], args.model))


if __name__ == '__main__':
    main()
