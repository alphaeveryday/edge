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
    parser.add_argument('--without-skills', action='store_true', help='Verify tools and output work without invoking Skill')
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
        prompt='This is a runtime smoke test. '
               + ('Do not invoke Skill. ' if args.without_skills else 'Load both available skills with Skill. ')
               + 'Call the runtime_probe tool once. Return only the status returned by that tool. '
               'Do not conduct ETF research or fetch the cited references.',
        schemas=[{'type':'function','function':{'name':'runtime_probe','description':'Return runtime status',
                 'parameters':{'type':'object','properties':{},'additionalProperties':False}}}],
        call=call, output_schema=schema, artifacts=args.artifacts, **settings))
    if calls != ['runtime_probe']:
        raise ValueError('Probe did not execute exactly once through the internal MCP')
    if args.without_skills:
        events = (args.artifacts/'events.jsonl').read_text(encoding='utf-8').splitlines()
        for row in events:
            message = json.loads(row)['message']
            content = message.get('content', [])
            if isinstance(content, list) and any(isinstance(block, dict) and block.get('name') == 'Skill' for block in content):
                raise ValueError('The no-skill probe unexpectedly invoked Skill')
    print(json.dumps({'status':result['status'], 'internal_mcp_calls':len(calls)}))


if __name__ == '__main__':
    main()
