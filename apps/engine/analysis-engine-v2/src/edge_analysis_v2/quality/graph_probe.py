"""Run a real agent on one probe question with the registered graph tools and judge its tool use.

The run folder keeps the answer, every stored tool response, the model events and one verdict
per hypothesis. Answer quality is not graded here; only the stated tool-use hypotheses are.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from time import perf_counter
from uuid import uuid4

from edge_analysis_v2.agent.runner import run_model
from edge_analysis_v2.quality.tool_intent import check
from edge_analysis_v2.tools.graph.catalog import load_catalog
from edge_analysis_v2.tools.graph.facts import open_reader
from edge_analysis_v2.tools.graph.provider import GraphTools

INTENTS = Path(__file__).with_name('tool_intents.json')
PROMPT = '''한국 주식·ETF 분석가로서 질문에 한국어로 답한다. 사실은 제공된 도구로 직접 확인한 것만 쓴다.
도구로 확인하지 못한 것은 확인하지 못했다고 쓰고, 요청한 대상을 다른 대상으로 바꾸지 않는다.
최종 주장마다 그 근거가 된 tool_run_id를 적는다. 사실 근거는 evidence_role=support,
도구가 거절하거나 자료가 없었던 사정을 설명하는 주장은 limitation으로 표시한다.
answer는 고객에게 보여 줄 답이며 내부 ID와 도구 이름을 쓰지 않는다.'''
OUTPUT = {'type': 'object', 'additionalProperties': False, 'required': ['answer', 'limitations', 'claims'], 'properties': {
    'answer': {'type': 'string', 'minLength': 1}, 'limitations': {'type': 'array', 'items': {'type': 'string'}},
    'claims': {'type': 'array', 'minItems': 1, 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['claim', 'tool_run_ids', 'purpose', 'evidence_role'], 'properties': {
            'claim': {'type': 'string', 'minLength': 1}, 'purpose': {'type': 'string', 'minLength': 1},
            'tool_run_ids': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
            'evidence_role': {'enum': ['support', 'limitation']}}}}}}


def usage(directory):
    """Sum the token counts the SDK reported for this conversation."""
    totals, path = {}, directory/'model/events.jsonl'
    if path.exists():
        for line in path.read_text(encoding='utf8').splitlines():
            event = json.loads(line)
            if event['message_type'] == 'ResultMessage':
                for key, value in (event['message'].get('usage') or {}).items():
                    if key.endswith('tokens') and type(value) is int:
                        totals[key] = totals.get(key, 0) + value
    return totals


async def run_probe(probe_id, probe, hypotheses, *, run, catalog, digest, runs_dir, key, model, model_call=run_model):
    """Execute one probe once and write its evidence folder.

    Args:
        probe_id: Identifier used as the run folder prefix.
        probe: Question and analysis cutoff.
        hypotheses: Rows of this probe from the intent file.
        run: Read-only graph query callable.
        catalog: Reviewed graph catalog.
        digest: SHA-256 of the catalog bytes.
        runs_dir: Parent folder of run folders.
        key: Model API key; never written.
        model: Model name.
        model_call: Replaced by a fake in offline tests.

    Returns:
        The stored report including one verdict per hypothesis.
    """
    directory = Path(runs_dir)/(probe_id + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:6])  # repeats can start within one second
    directory.mkdir(parents=True)
    tools = GraphTools(run, catalog, directory/'tools', probe['cutoff'])
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=Path(__file__).parent,
                            capture_output=True, text=True).stdout.strip()
    report = {'case': probe_id, 'kind': 'tool_intent_probe', 'question': probe['question'], 'cutoff': probe['cutoff'],
              'model': model, 'execution_host': 'local runner with cloud PuppyGraph', 'code_commit': commit,
              'catalog_hash': digest, 'registered_tools': [s['function']['name'] for s in tools.schemas], 'status': 'running'}
    started = perf_counter()
    try:
        report['response'] = await model_call(initial={'question': probe['question'], 'analysis_cutoff': probe['cutoff']},
            prompt=PROMPT, schemas=tools.schemas, call=tools.call, output_schema=OUTPUT,
            artifacts=directory/'model', key=key, model=model, timeout_seconds=600)
        report['status'] = 'answered'
    except Exception as exc:
        report.update(status='error', error=(type(exc).__name__ + ': ' + str(exc)).replace(key, '[redacted]'))
    calls = tools.store.calls
    report.update(elapsed_ms=round((perf_counter() - started) * 1000, 2), tool_calls=len(calls),
                  graph_queries=len(tools.graph.queries), tools_used=[c['tool'] for c in calls],
                  tool_errors=sum(bool(c['error']) for c in calls), usage=usage(directory),
                  intent=check(hypotheses, calls, report.get('response') or {}))
    (directory/'benchmark.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    return report | {'path': str(directory)}


async def main(args):
    intents = json.loads(INTENTS.read_text(encoding='utf8'))
    catalog, digest = load_catalog()
    selected = [(probe_id, probe, [h for t in intents['tools'].values() for h in t['hypotheses'] if h['probe'] == probe_id])
                for probe_id, probe in intents['probes'].items() if probe_id in args.probes]
    if len(selected) != len(set(args.probes)):
        raise ValueError('Unknown probe')
    with open_reader(os.environ['GRAPH_URI'], os.environ['GRAPH_USERNAME'], os.environ['GRAPH_PASSWORD']) as run:
        for probe_id, probe, hypotheses in selected:
            for _ in range(args.repeat):
                report = await run_probe(probe_id, probe, hypotheses, run=run, catalog=catalog, digest=digest,
                    runs_dir=args.runs_dir, key=os.environ['DEEPSEEK_API_KEY'], model=args.model)
                print(json.dumps({'path': report['path'], 'status': report['status'], 'tools': report['tools_used'],
                    'input_tokens': sum(v for k, v in report['usage'].items() if 'input' in k),
                    'intent': {v['id']: v['status'] for v in report['intent']}}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('probes', nargs='+')
    parser.add_argument('--repeat', type=int, default=3)
    parser.add_argument('--runs-dir', type=Path, required=True)
    parser.add_argument('--model', default='deepseek-flash')
    asyncio.run(main(parser.parse_args()))
