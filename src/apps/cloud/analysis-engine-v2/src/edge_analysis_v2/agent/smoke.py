"""Explicit paid SDK/DeepSeek compatibility probe; never connects to an analysis DB."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from edge_analysis_v2.agent.runner import run_model
from edge_analysis_v2.dashboard.jobs import read_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    settings = read_settings(args.env_file) if args.env_file else {
        'key':os.environ.get('DEEPSEEK_API_KEY',''),
        'model':os.environ.get('DEEPSEEK_MODEL','deepseek-v4-flash')}
    calls = []
    def call(name, arguments):
        calls.append(name)
        return {'tool_run_id':'smoke-only', 'result':{'status':'ready'}}
    schema = {'type':'object', 'required':['status'], 'additionalProperties':False,
              'properties':{'status':{'const':'ready'}}}
    result = asyncio.run(run_model(initial={'purpose':'SDK skill compatibility test, not an ETF report'},
        prompt='This is a runtime smoke test. Load both required skills with Skill, then call '
               'the runtime_probe tool once. Return only the status returned by that tool. '
               'Do not conduct ETF research or fetch the cited references.',
        schemas=[{'type':'function','function':{'name':'runtime_probe','description':'Return runtime status',
                 'parameters':{'type':'object','properties':{},'additionalProperties':False}}}],
        call=call, output_schema=schema, artifacts=args.artifacts, **settings))
    if calls != ['runtime_probe']:
        raise ValueError('Probe did not execute exactly once through the internal MCP')
    print(json.dumps({'status':result['status'], 'internal_mcp_calls':len(calls)}))


if __name__ == '__main__':
    main()
